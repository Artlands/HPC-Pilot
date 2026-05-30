from __future__ import annotations

import pytest

import hpc_agent.tools.spack as spack_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.spack import FindIn, ListEnvsIn, SpecIn, find, list_envs, spec


class FakeSpackRunner:
    def __init__(self, *, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, command: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(command.argv)
        if command.argv[-2:] == ["env", "list"]:
            stdout = "==> 2 environments\ncore-tools\ngpu-stack\n"
        elif "find" in command.argv:
            stdout = "==> 2 installed packages\n/abc123 gcc@13.2.0\n/def456 openmpi@5.0.0\n"
        else:
            stdout = "Input spec\n  fftw\nConcretized\n  fftw@3.3.10\n"
        return CommandResult(rc=self.rc, stdout=stdout, stderr="spack failed", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeSpackRunner) -> None:
    monkeypatch.setattr(spack_mod, "run_command", runner)


def test_list_envs_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = list_envs(ListEnvsIn(), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.OK
    assert res.data == {"envs": ["core-tools", "gpu-stack"]}
    assert runner.calls == [[spack_mod.SPACK, "env", "list"]]


def test_find_parseable_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = find(FindIn(env="gpu-stack"), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.OK
    assert res.data == {
        "env": "gpu-stack",
        "specs": [
            {"hash": "/abc123", "spec": "gcc@13.2.0"},
            {"hash": "/def456", "spec": "openmpi@5.0.0"},
        ],
    }
    assert runner.calls == [[spack_mod.SPACK, "-e", "gpu-stack", "find", "-P"]]


def test_spec_preview(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner()
    _patch_runner(monkeypatch, runner)

    res = spec(SpecIn(spec="fftw"), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.OK
    assert res.data and res.data["spec"] == "fftw"
    assert "fftw@3.3.10" in str(res.data["concretization"])
    assert runner.calls == [[spack_mod.SPACK, "spec", "-I", "fftw"]]


def test_query_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeSpackRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = list_envs(ListEnvsIn(), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
