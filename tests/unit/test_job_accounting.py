from __future__ import annotations

import json

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import JobAccountingIn, job_accounting


class FakeAccountingRunner:
    def __init__(
        self, *, jobs: list[dict[str, object]] | None = None, invalid: bool = False
    ) -> None:
        self.calls: list[list[str]] = []
        self.jobs = jobs if jobs is not None else []
        self.invalid = invalid

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        stdout = "{bad-json" if self.invalid else json.dumps({"jobs": self.jobs})
        return CommandResult(rc=0, stdout=stdout, stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeAccountingRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_job_accounting_filters_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountingRunner(
        jobs=[
            {
                "job_id": 123,
                "name": "train",
                "user": "alice",
                "account": "research",
                "partition": "gpu",
                "state": "COMPLETED",
                "elapsed": "00:10:00",
                "exit_code": "0:0",
            }
        ]
    )
    _patch_runner(monkeypatch, runner)

    res = job_accounting(
        JobAccountingIn(
            user="alice",
            account="research",
            start="2026-05-01",
            end="2026-05-02",
            state="COMPLETED",
        ),
        actor="alice",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {
        "jobs": [
            {
                "job_id": 123,
                "name": "train",
                "user": "alice",
                "account": "research",
                "partition": "gpu",
                "state": "COMPLETED",
                "elapsed": "00:10:00",
                "start": None,
                "end": None,
                "exit_code": "0:0",
            }
        ]
    }
    assert runner.calls == [
        [
            slurm_mod.SACCT,
            "--json",
            "--user=alice",
            "--account=research",
            "--starttime=2026-05-01",
            "--endtime=2026-05-02",
            "--state=COMPLETED",
        ]
    ]


def test_job_accounting_all(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountingRunner()
    _patch_runner(monkeypatch, runner)

    res = job_accounting(JobAccountingIn(), actor="alice")

    assert res.status == ToolStatus.OK
    assert runner.calls == [[slurm_mod.SACCT, "--json"]]


def test_job_accounting_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAccountingRunner(invalid=True)
    _patch_runner(monkeypatch, runner)

    res = job_accounting(JobAccountingIn(), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
