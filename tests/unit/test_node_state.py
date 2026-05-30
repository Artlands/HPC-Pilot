from __future__ import annotations

import json

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety import gate as safety_gate
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import NodeStateIn, node_state


class FakeNodeRunner:
    def __init__(self, node: dict[str, object] | None) -> None:
        self.calls: list[list[str]] = []
        self.node = node

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if spec.argv[:3] == [slurm_mod.SCONTROL, "show", "node"]:
            stdout = json.dumps({"nodes": [self.node]}) if self.node is not None else '{"nodes":[]}'
            return CommandResult(rc=0, stdout=stdout, stderr="", duration_s=0.0)
        return CommandResult(rc=0, stdout="", stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeNodeRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_node_drain_dry_run_mutates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeNodeRunner({"name": "gpu01", "state": ["IDLE"], "reason": ""})
    _patch_runner(monkeypatch, runner)

    inp = NodeStateIn(node="gpu01", target="drain", reason="maintenance", dry_run=True)
    res = node_state(inp, actor="alice")

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert all("update" not in call for call in runner.calls)


def test_node_drain_requires_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeNodeRunner({"name": "gpu01", "state": ["IDLE"], "reason": ""})
    _patch_runner(monkeypatch, runner)

    res = node_state(NodeStateIn(node="gpu01", target="drain", dry_run=False), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"
    assert runner.calls == []


def test_node_drain_apply_records_resume_inverse(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeNodeRunner({"name": "gpu01", "state": ["IDLE"], "reason": ""})
    _patch_runner(monkeypatch, runner)

    inp = NodeStateIn(node="gpu01", target="drain", reason="maintenance", dry_run=False)
    res = node_state(
        inp,
        actor="alice",
        gate_override=safety_gate.Gate(requires_approval=True, approved=True, approver="lead"),
    )

    assert res.status == ToolStatus.OK
    update_calls = [call for call in runner.calls if "update" in call]
    assert update_calls
    assert "State=DRAIN" in update_calls[0]
    assert "Reason=maintenance" in update_calls[0]

    event = audit.get_event(res.audit_id or "")
    assert event is not None and event.revert_argv
    assert any("State=RESUME" in tok for argv in event.revert_argv for tok in argv)


def test_node_down_requires_approval_without_override(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeNodeRunner({"name": "gpu01", "state": ["IDLE"], "reason": ""})
    _patch_runner(monkeypatch, runner)

    inp = NodeStateIn(node="gpu01", target="down", reason="hardware failure", dry_run=False)
    res = node_state(inp, actor="alice")

    assert res.status == ToolStatus.NEEDS_APPROVAL
    assert all("update" not in call for call in runner.calls)


def test_node_resume_idempotent_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeNodeRunner({"name": "gpu01", "state": ["IDLE"], "reason": ""})
    _patch_runner(monkeypatch, runner)

    res = node_state(NodeStateIn(node="gpu01", target="resume", dry_run=False), actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data and res.data.get("noop") is True
    assert all("update" not in call for call in runner.calls)


def test_node_missing_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeNodeRunner(None)
    _patch_runner(monkeypatch, runner)

    inp = NodeStateIn(node="ghost", target="resume", dry_run=False)
    res = node_state(inp, actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "not_found"
