from __future__ import annotations

import pytest

import hpc_agent.tools.spack as spack_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety import gate as safety_gate
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.spack import BuildcacheIn, manage_buildcache


class FakeBuildcacheRunner:
    def __init__(self, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, command: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(command.argv)
        stdout = f"buildcache {spack_mod.settings.spack_root}/mirrors/default\n"
        return CommandResult(rc=self.rc, stdout=stdout, stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeBuildcacheRunner) -> None:
    monkeypatch.setattr(spack_mod, "run_command", runner)


def test_buildcache_push_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeBuildcacheRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_buildcache(
        BuildcacheIn(op="push", mirror="/path/to/mirror", dry_run=True),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_buildcache_push_apply(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeBuildcacheRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_buildcache(
        BuildcacheIn(op="push", mirror="/path/to/mirror", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.OK
    assert res.data == {"op": "push", "mirror": "/path/to/mirror"}
    assert runner.calls == [[spack_mod.SPACK, "buildcache", "push", "/path/to/mirror"]]


def test_buildcache_update_index(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeBuildcacheRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_buildcache(
        BuildcacheIn(op="update_index", mirror="/path/to/mirror", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.OK
    assert runner.calls == [[spack_mod.SPACK, "buildcache", "update-index", "/path/to/mirror"]]


def test_buildcache_add_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeBuildcacheRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_buildcache(
        BuildcacheIn(op="add_mirror", mirror="https://example.com/mirror", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.OK
    assert runner.calls == [
        [
            spack_mod.SPACK,
            "mirror",
            "add",
            "https---example.com-mirror",
            "https://example.com/mirror",
        ]
    ]


def test_buildcache_with_key_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeBuildcacheRunner()
    _patch_runner(monkeypatch, runner)

    res = manage_buildcache(
        BuildcacheIn(op="push", mirror="/path/to/mirror", signing_key_ref="ABC123", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.OK
    assert res.error is None


def test_buildcache_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeBuildcacheRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = manage_buildcache(
        BuildcacheIn(op="push", mirror="/path/to/mirror", dry_run=False),
        actor="alice",
        actor_role=Role.OPERATOR,
        gate_override=safety_gate.Gate(approved=True),
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
