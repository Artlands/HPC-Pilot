from __future__ import annotations

import json

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety import gate as safety_gate
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import ManageReservationIn, manage_reservation

RESERVATION = {
    "name": "maint-gpu",
    "nodes": ["gpu01", "gpu02"],
    "start_time": "2026-06-01T01:00:00",
    "duration": 60,
    "users": ["root"],
    "flags": ["MAINT", "IGNORE_JOBS"],
}


class FakeReservationRunner:
    def __init__(self, reservation: dict[str, object] | None, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.reservation = reservation
        self.rc = rc

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if spec.argv[:3] == [slurm_mod.SCONTROL, "show", "reservation"]:
            stdout = (
                json.dumps({"reservations": [self.reservation]})
                if self.reservation is not None
                else '{"reservations":[]}'
            )
            return CommandResult(rc=0, stdout=stdout, stderr="", duration_s=0.0)
        return CommandResult(rc=self.rc, stdout="", stderr="reservation error", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeReservationRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_reservation_create_requires_core_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReservationRunner(None)
    _patch_runner(monkeypatch, runner)

    res = manage_reservation(
        ManageReservationIn(name="maint-gpu", op="create", nodes=["gpu01"], dry_run=False),
        actor="alice",
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"
    assert runner.calls == []


def test_reservation_create_dry_run_mutates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReservationRunner(None)
    _patch_runner(monkeypatch, runner)

    inp = ManageReservationIn(
        name="maint-gpu",
        op="create",
        nodes=["gpu01"],
        start="2026-06-01T01:00:00",
        duration_min=60,
        users=["root"],
        dry_run=True,
    )
    res = manage_reservation(inp, actor="alice")

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert all("create" not in call for call in runner.calls)


def test_reservation_create_requires_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReservationRunner(None)
    _patch_runner(monkeypatch, runner)

    inp = ManageReservationIn(
        name="maint-gpu",
        op="create",
        nodes=["gpu01"],
        start="2026-06-01T01:00:00",
        duration_min=60,
        dry_run=False,
    )
    res = manage_reservation(inp, actor="alice")

    assert res.status == ToolStatus.NEEDS_APPROVAL
    assert all("create" not in call for call in runner.calls)


def test_reservation_create_apply_records_delete_inverse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = FakeReservationRunner(None)
    _patch_runner(monkeypatch, runner)

    inp = ManageReservationIn(
        name="maint-gpu",
        op="create",
        nodes=["gpu01"],
        start="2026-06-01T01:00:00",
        duration_min=60,
        dry_run=False,
    )
    res = manage_reservation(
        inp,
        actor="alice",
        gate_override=safety_gate.Gate(requires_approval=True, approved=True, approver="lead"),
    )

    assert res.status == ToolStatus.OK
    create_calls = [call for call in runner.calls if "create" in call]
    assert create_calls
    assert "ReservationName=maint-gpu" in create_calls[0]

    event = audit.get_event(res.audit_id or "")
    assert event is not None and event.revert_argv
    assert any("reservation=maint-gpu" in tok for argv in event.revert_argv for tok in argv)


def test_reservation_delete_records_create_inverse(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReservationRunner(RESERVATION)
    _patch_runner(monkeypatch, runner)

    inp = ManageReservationIn(name="maint-gpu", op="delete", dry_run=False)
    res = manage_reservation(
        inp,
        actor="alice",
        gate_override=safety_gate.Gate(requires_approval=True, approved=True, approver="lead"),
    )

    assert res.status == ToolStatus.OK
    delete_calls = [call for call in runner.calls if "delete" in call]
    assert delete_calls == [[slurm_mod.SCONTROL, "delete", "reservation=maint-gpu"]]

    event = audit.get_event(res.audit_id or "")
    assert event is not None and event.revert_argv
    inverse_tokens = [tok for argv in event.revert_argv for tok in argv]
    assert "ReservationName=maint-gpu" in inverse_tokens
    assert "Nodes=gpu01,gpu02" in inverse_tokens
    assert "Duration=60" in inverse_tokens


def test_reservation_delete_missing_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReservationRunner(None)
    _patch_runner(monkeypatch, runner)

    res = manage_reservation(
        ManageReservationIn(name="ghost", op="delete", dry_run=False),
        actor="alice",
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "not_found"
    assert all("delete" not in call for call in runner.calls)
