from __future__ import annotations

import pytest

import hpc_agent.tools.spack as spack_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety import gate as safety_gate
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.spack import InstallIn, install_packages


class FakeInstallRunner:
    def __init__(self, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, command: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(command.argv)
        stdout = "Installed 5 packages\n"
        return CommandResult(rc=self.rc, stdout=stdout, stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeInstallRunner) -> None:
    monkeypatch.setattr(spack_mod, "run_command", runner)


def test_install_packages_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeInstallRunner()
    _patch_runner(monkeypatch, runner)

    res = install_packages(
        InstallIn(env="gpu-stack", dry_run=True),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_install_packages_apply_default(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeInstallRunner()
    _patch_runner(monkeypatch, runner)

    res = install_packages(
        InstallIn(env="gpu-stack", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "gpu-stack", "jobs": 16}
    expected = [spack_mod.SPACK, "-e", "gpu-stack", "install", "--use-buildcache", "-j", "16"]
    assert runner.calls == [expected]


def test_install_packages_no_buildcache(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeInstallRunner()
    _patch_runner(monkeypatch, runner)

    res = install_packages(
        InstallIn(env="gpu-stack", use_buildcache=False, jobs=8, dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "gpu-stack", "jobs": 8}
    expected = [spack_mod.SPACK, "-e", "gpu-stack", "install", "-j", "8"]
    assert runner.calls == [expected]


def test_install_packages_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeInstallRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = install_packages(
        InstallIn(env="gpu-stack", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
