from __future__ import annotations

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety import gate as safety_gate
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import ExtendAccountIn, extend_account

ACCOUNT_ROW = (
    "Account|Descr|Org|ParentName|GrpTRES|MaxWall\n"
    "research|Research account|science|root|cpu=512|1-00:00:00\n"
)


class FakeAccountRunner:
    def __init__(self, current_row: str | None) -> None:
        self.calls: list[list[str]] = []
        self.current_row = current_row

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if spec.argv[:3] == [slurm_mod.SACCTMGR, "show", "account"]:
            return CommandResult(
                rc=0,
                stdout=self.current_row or "",
                stderr="",
                duration_s=0.0,
            )
        return CommandResult(rc=0, stdout="", stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeAccountRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_account_dry_run_mutates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountRunner(ACCOUNT_ROW)
    _patch_runner(monkeypatch, runner)

    inp = ExtendAccountIn(
        name="research",
        op="modify",
        max_wall_min=2880,
        dry_run=True,
    )
    res = extend_account(inp, actor="alice")

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert all("modify" not in call and "add" not in call for call in runner.calls)


def test_account_modify_apply_records_inverse(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountRunner(ACCOUNT_ROW)
    _patch_runner(monkeypatch, runner)

    inp = ExtendAccountIn(
        name="research",
        op="modify",
        max_wall_min=2880,
        grp_tres="cpu=1024",
        dry_run=False,
    )
    res = extend_account(
        inp,
        actor="alice",
        gate_override=safety_gate.Gate(requires_approval=True, approved=True, approver="lead"),
    )

    assert res.status == ToolStatus.OK
    modify_calls = [call for call in runner.calls if "modify" in call]
    assert modify_calls
    assert "MaxWall=2-00:00:00" in modify_calls[0]
    assert "GrpTRES=cpu=1024" in modify_calls[0]

    event = audit.get_event(res.audit_id or "")
    assert event is not None and event.revert_argv
    inverse_tokens = [tok for argv in event.revert_argv for tok in argv]
    assert "MaxWall=1-00:00:00" in inverse_tokens
    assert "GrpTRES=cpu=512" in inverse_tokens


def test_account_modify_missing_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountRunner(None)
    _patch_runner(monkeypatch, runner)

    inp = ExtendAccountIn(name="ghost", op="modify", parent="root", dry_run=False)
    res = extend_account(inp, actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "not_found"
    assert all("modify" not in call for call in runner.calls)


def test_account_create_requires_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountRunner(None)
    _patch_runner(monkeypatch, runner)

    inp = ExtendAccountIn(name="newacct", op="create", parent="root", dry_run=False)
    res = extend_account(inp, actor="alice")

    assert res.status == ToolStatus.NEEDS_APPROVAL
    assert all("add" not in call for call in runner.calls)


def test_account_idempotent_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountRunner(ACCOUNT_ROW)
    _patch_runner(monkeypatch, runner)

    inp = ExtendAccountIn(
        name="research",
        op="modify",
        parent="root",
        organization="science",
        description="Research account",
        grp_tres="cpu=512",
        max_wall_min=1440,
        dry_run=False,
    )
    res = extend_account(inp, actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data and res.data.get("noop") is True
    assert all("modify" not in call for call in runner.calls)
