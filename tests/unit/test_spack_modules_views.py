from __future__ import annotations

import pytest

import hpc_agent.tools.spack as spack_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.spack import CreateViewIn, GenModulesIn, create_view, generate_modules


class FakeModuleRunner:
    def __init__(self, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, command: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(command.argv)
        if "module lmod refresh" in " ".join(command.argv):
            stdout = "Changed lmod modules\n"
        else:
            stdout = "Changed tcl modules\n"
        return CommandResult(rc=self.rc, stdout=stdout, stderr="", duration_s=0.0)


class FakeViewRunner:
    def __init__(self, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, command: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(command.argv)
        stdout = "View created/symlinked\n"
        return CommandResult(rc=self.rc, stdout=stdout, stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_module_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeModuleRunner) -> None:
    monkeypatch.setattr(spack_mod, "run_command", runner)


def _patch_view_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeViewRunner) -> None:
    monkeypatch.setattr(spack_mod, "run_command", runner)


def test_generate_modules_lmod_default(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeModuleRunner()
    _patch_module_runner(monkeypatch, runner)

    res = generate_modules(GenModulesIn(env="gpu-stack"), actor="alice", actor_role=Role.OPERATOR)

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_generate_modules_lmod_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeModuleRunner()
    _patch_module_runner(monkeypatch, runner)

    res = generate_modules(
        GenModulesIn(env="gpu-stack", module_type="lmod", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "gpu-stack", "module_type": "lmod"}
    assert runner.calls == [
        [spack_mod.SPACK, "-e", "gpu-stack", "module", "lmod", "refresh", "--delete-tree", "-y"]
    ]


def test_generate_modules_tcl(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeModuleRunner()
    _patch_module_runner(monkeypatch, runner)

    res = generate_modules(
        GenModulesIn(env="core-tools", module_type="tcl", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "core-tools", "module_type": "tcl"}
    assert runner.calls == [
        [spack_mod.SPACK, "-e", "core-tools", "module", "tcl", "refresh", "--delete-tree", "-y"]
    ]


def test_generate_modules_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeModuleRunner(rc=1)
    _patch_module_runner(monkeypatch, runner)

    res = generate_modules(
        GenModulesIn(env="gpu-stack", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
    assert res.error and "rc=1" in res.error.message


def test_create_view_default_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeViewRunner()
    _patch_view_runner(monkeypatch, runner)

    res = create_view(
        CreateViewIn(env="gpu-stack", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "gpu-stack", "prefix": None}
    assert runner.calls == [[spack_mod.SPACK, "-e", "gpu-stack", "env", "view", "enable"]]


def test_create_view_custom_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeViewRunner()
    _patch_view_runner(monkeypatch, runner)

    res = create_view(
        CreateViewIn(env="gpu-stack", prefix="/opt/modules", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"env": "gpu-stack", "prefix": "/opt/modules"}
    assert runner.calls == [[spack_mod.SPACK, "-e", "gpu-stack", "view", "symlink", "/opt/modules"]]


def test_create_view_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeViewRunner()
    _patch_view_runner(monkeypatch, runner)

    res = create_view(
        CreateViewIn(env="gpu-stack", dry_run=True),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_create_view_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeViewRunner(rc=1)
    _patch_view_runner(monkeypatch, runner)

    res = create_view(
        CreateViewIn(env="gpu-stack", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
    assert res.error and "rc=1" in res.error.message
