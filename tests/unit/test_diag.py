from __future__ import annotations

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import DiagIn, diag


class FakeDiagRunner:
    def __init__(self, *, fail_binary: str | None = None) -> None:
        self.calls: list[list[str]] = []
        self.fail_binary = fail_binary

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if self.fail_binary is not None and spec.argv[0] == self.fail_binary:
            return CommandResult(rc=1, stdout="", stderr="failed", duration_s=0.0)
        if spec.argv[:3] == [slurm_mod.SCONTROL, "show", "config"]:
            return CommandResult(
                rc=0,
                stdout="ClusterName = auto\nSlurmctldHost = ctrl01\n",
                stderr="",
                duration_s=0.0,
            )
        if spec.argv == [slurm_mod.SDIAG]:
            return CommandResult(
                rc=0,
                stdout="Server thread count: 8\nAgent queue size: 0\n",
                stderr="",
                duration_s=0.0,
            )
        return CommandResult(rc=1, stdout="", stderr="unexpected", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeDiagRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_diag_reads_config_and_sdiag_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeDiagRunner()
    _patch_runner(monkeypatch, runner)

    res = diag(DiagIn(), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.OK
    assert res.data == {
        "config": {"ClusterName": "auto", "SlurmctldHost": "ctrl01"},
        "sdiag": {"Server thread count": "8", "Agent queue size": "0"},
    }
    assert runner.calls == [
        [slurm_mod.SCONTROL, "show", "config"],
        [slurm_mod.SDIAG],
    ]


def test_diag_can_read_config_only(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeDiagRunner()
    _patch_runner(monkeypatch, runner)

    res = diag(DiagIn(include_sdiag=False), actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data == {"config": {"ClusterName": "auto", "SlurmctldHost": "ctrl01"}}
    assert runner.calls == [[slurm_mod.SCONTROL, "show", "config"]]


def test_diag_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeDiagRunner(fail_binary=slurm_mod.SDIAG)
    _patch_runner(monkeypatch, runner)

    res = diag(DiagIn(), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
