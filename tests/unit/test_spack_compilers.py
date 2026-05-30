from __future__ import annotations

import pytest

import hpc_agent.tools.spack as spack_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.spack import ManageCompilersIn, manage_compilers


class FakeSpackRunner:
    def __init__(self, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, command: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(command.argv)
        if "compiler find" in " ".join(command.argv):
            stdout = "==> 2 compilers\n  gcc@13.2.0\n  clang@15.0.0\n"
        else:
            stdout = "Added compiler\n  gcc@13.2.0\n"
        return CommandResult(rc=self.rc, stdout=stdout, stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeSpackRunner) -> None:
    monkeypatch.setattr(spack_mod, "run_command", runner)


def test_compiler_find_default_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_compilers(
        ManageCompilersIn(op="find", scope="site", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"op": "find", "scope": "site", "path": None}
    assert runner.calls == [[spack_mod.SPACK, "compiler", "find", "--scope", "site"]]


def test_compiler_find_custom_path(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_compilers(
        ManageCompilersIn(op="find", scope="site", path="/usr/local/bin", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert runner.calls == [
        [spack_mod.SPACK, "compiler", "find", "--scope", "site", "/usr/local/bin"]
    ]


def test_compiler_add_default_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_compilers(
        ManageCompilersIn(op="add", scope="env", env="gpu-stack", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"op": "add", "scope": "env", "path": None}
    assert runner.calls == [[spack_mod.SPACK, "compiler", "add", "--scope", "env"]]


def test_compiler_add_with_path(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_compilers(
        ManageCompilersIn(op="add", scope="site", path="/opt/gcc/bin", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.OK
    assert runner.calls == [[spack_mod.SPACK, "compiler", "add", "--scope", "site", "/opt/gcc/bin"]]


def test_compiler_dry_run_does_not_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_compilers(
        ManageCompilersIn(op="find", scope="site", dry_run=True),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_compiler_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = manage_compilers(
        ManageCompilersIn(op="find", scope="site", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
    assert res.error and "rc=1" in res.error.message


def test_compiler_find_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_compilers(
        ManageCompilersIn(op="find", scope="env", env="core-tools", dry_run=False),
        actor="bob",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.DENIED
    assert res.error is not None
    assert "lacks" in res.error.message or "denied" in res.error.message.lower()
