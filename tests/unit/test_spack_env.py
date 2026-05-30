from __future__ import annotations

import pytest

import hpc_agent.tools.spack as spack_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.spack import ManageEnvIn, manage_environment


class FakeEnvRunner:
    def __init__(self, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, command: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(command.argv)
        if "env create" in " ".join(command.argv):
            stdout = f"Created environment '{spack_mod.settings.spack_root}/envs/test-env'\n"
        else:
            stdout = f"Modified environment '{spack_mod.settings.spack_root}/envs/test-env'\n"
        return CommandResult(rc=self.rc, stdout=stdout, stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeEnvRunner) -> None:
    monkeypatch.setattr(spack_mod, "run_command", runner)


def test_manage_env_create_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeEnvRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_environment(
        ManageEnvIn(name="test-env", op="create", dry_run=True),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_manage_env_create_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeEnvRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_environment(
        ManageEnvIn(name="test-env", op="create", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "test-env", "op": "create"}
    assert runner.calls == [[spack_mod.SPACK, "env", "create", "test-env"]]


def test_manage_env_add_specs_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeEnvRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_environment(
        ManageEnvIn(name="test-env", op="add_specs", specs=["gcc@13", "openmpi"], dry_run=True),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_manage_env_add_specs_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeEnvRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_environment(
        ManageEnvIn(name="test-env", op="add_specs", specs=["gcc@13", "openmpi"], dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "test-env", "op": "add_specs"}
    assert runner.calls == [[spack_mod.SPACK, "env", "edit", "test-env"]]


def test_manage_env_remove_specs_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeEnvRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_environment(
        ManageEnvIn(name="test-env", op="remove_specs", specs=["gcc@13"], dry_run=True),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_manage_env_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeEnvRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = manage_environment(
        ManageEnvIn(name="test-env", op="create", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
    assert res.error and "rc=1" in res.error.message
