from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import ManageQOSIn, manage_qos

POLICY_DIR = Path(__file__).resolve().parents[2] / "config_repo" / "policy"


class FakeRunner:
    """Records argv and returns canned results keyed by the sacctmgr subcommand."""

    def __init__(self, current_row: str | None) -> None:
        self.calls: list[list[str]] = []
        self.current_row = current_row  # the '-P' show output, or None

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if "show" in spec.argv:
            if self.current_row is None:
                return CommandResult(rc=0, stdout="", stderr="", duration_s=0.0)
            return CommandResult(rc=0, stdout=self.current_row, stderr="", duration_s=0.0)
        # add/modify
        return CommandResult(rc=0, stdout="", stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


GPU_ROW = (
    "Name|Priority|MaxWall|MaxJobsPU|MaxTRES|MaxTRESPU|GrpTRES\n"
    "gpu|100|1-00:00:00||gres/gpu=8||\n"
)


def test_dry_run_mutates_nothing(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeRunner(current_row=GPU_ROW)
    _patch_runner(monkeypatch, runner)
    inp = ManageQOSIn(name="gpu", op="modify", max_wall_min=2880, dry_run=True)
    res = manage_qos(inp, actor="alice", policy=policy)
    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    # only the `show` query ran; no add/modify
    assert all("modify" not in c and "add" not in c for c in runner.calls)


def test_inpolicy_modify_autoapplies(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeRunner(current_row=GPU_ROW)
    _patch_runner(monkeypatch, runner)
    inp = ManageQOSIn(name="gpu", op="modify", max_wall_min=2880, dry_run=False)
    res = manage_qos(inp, actor="alice", policy=policy)
    assert res.status == ToolStatus.OK
    # a modify command was issued with the converted time
    modify_calls = [c for c in runner.calls if "modify" in c]
    assert modify_calls
    assert any("MaxWall=2-00:00:00" in tok for tok in modify_calls[0])
    # inverse recorded for revert
    event = audit.get_event(res.audit_id or "")
    assert event is not None and event.revert_argv
    assert any("MaxWall=1-00:00:00" in tok for argv in event.revert_argv for tok in argv)


def test_outofpolicy_wall_denied(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeRunner(current_row=GPU_ROW)
    _patch_runner(monkeypatch, runner)
    inp = ManageQOSIn(name="gpu", op="modify", max_wall_min=100000, dry_run=False)
    res = manage_qos(inp, actor="alice", policy=policy)
    assert res.status == ToolStatus.DENIED
    assert all("modify" not in c for c in runner.calls)  # never executed the mutation


def test_idempotent_noop(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    # current already has MaxWall=1 day; requesting 1440 min => no change
    runner = FakeRunner(current_row=GPU_ROW)
    _patch_runner(monkeypatch, runner)
    inp = ManageQOSIn(name="gpu", op="modify", max_wall_min=1440, dry_run=False)
    res = manage_qos(inp, actor="alice", policy=policy)
    assert res.status == ToolStatus.OK
    assert res.data and res.data.get("noop") is True
    assert all("modify" not in c for c in runner.calls)


def test_modify_missing_qos_not_found(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeRunner(current_row=None)
    _patch_runner(monkeypatch, runner)
    inp = ManageQOSIn(name="ghost", op="modify", max_wall_min=60, dry_run=False)
    res = manage_qos(inp, actor="alice", policy=policy)
    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "not_found"


def test_create_requires_approval(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeRunner(current_row=None)
    _patch_runner(monkeypatch, runner)
    inp = ManageQOSIn(name="newq", op="create", priority=50, dry_run=False)
    res = manage_qos(inp, actor="alice", policy=policy)
    assert res.status == ToolStatus.NEEDS_APPROVAL
    assert all("add" not in c for c in runner.calls)
