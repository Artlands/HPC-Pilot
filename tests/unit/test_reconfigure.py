from __future__ import annotations

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import ReconfigureIn, reconfigure


class FakeReconfigureRunner:
    def __init__(self, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        return CommandResult(rc=self.rc, stdout="", stderr="bad config", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeReconfigureRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_reconfigure_dry_run_mutates_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReconfigureRunner()
    _patch_runner(monkeypatch, runner)

    res = reconfigure(ReconfigureIn(dry_run=True), actor="alice")

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_reconfigure_autoapplies(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReconfigureRunner()
    _patch_runner(monkeypatch, runner)

    res = reconfigure(ReconfigureIn(dry_run=False), actor="alice")

    assert res.status == ToolStatus.OK
    assert runner.calls == [[slurm_mod.SCONTROL, "reconfigure"]]


def test_reconfigure_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeReconfigureRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = reconfigure(ReconfigureIn(dry_run=False), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
