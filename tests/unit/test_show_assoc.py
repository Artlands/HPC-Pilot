from __future__ import annotations

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import ShowAssocIn, show_assoc

ASSOC_ROWS = (
    "User|Account|QOS|DefaultQOS|FairShare\n"
    "alice|research|normal,gpu|normal|100\n"
    "bob|research|normal|normal|50\n"
)


class FakeShowAssocRunner:
    def __init__(self, *, rc: int = 0, stdout: str = ASSOC_ROWS) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc
        self.stdout = stdout

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        return CommandResult(
            rc=self.rc, stdout=self.stdout, stderr="db unavailable", duration_s=0.0
        )


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeShowAssocRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_show_assoc_filters_user_and_account_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeShowAssocRunner()
    _patch_runner(monkeypatch, runner)

    res = show_assoc(
        ShowAssocIn(user="alice", account="research"),
        actor="alice",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.OK
    assert res.data and len(res.data["associations"]) == 2
    assert runner.calls == [
        [
            slurm_mod.SACCTMGR,
            "show",
            "assoc",
            "user=alice",
            "account=research",
            "-P",
            "--noheader=no",
        ]
    ]


def test_show_assoc_all(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeShowAssocRunner()
    _patch_runner(monkeypatch, runner)

    res = show_assoc(ShowAssocIn(), actor="alice")

    assert res.status == ToolStatus.OK
    assert runner.calls == [[slurm_mod.SACCTMGR, "show", "assoc", "-P", "--noheader=no"]]


def test_show_assoc_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeShowAssocRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = show_assoc(ShowAssocIn(user="alice"), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
