from __future__ import annotations

import json

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import QueueIn, queue


class FakeQueueRunner:
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


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeQueueRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_queue_filters_user_partition_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeQueueRunner(
        jobs=[
            {
                "job_id": 123,
                "name": "train",
                "user_name": "alice",
                "partition": "gpu",
                "job_state": "PENDING",
                "state_reason": "Resources",
            }
        ]
    )
    _patch_runner(monkeypatch, runner)

    res = queue(QueueIn(user="alice", partition="gpu"), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.OK
    assert res.data == {
        "jobs": [
            {
                "job_id": 123,
                "name": "train",
                "user": "alice",
                "partition": "gpu",
                "state": "PENDING",
                "nodes": None,
                "reason": "Resources",
            }
        ]
    }
    assert runner.calls == [[slurm_mod.SQUEUE, "--json", "--user=alice", "--partition=gpu"]]


def test_queue_all(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeQueueRunner()
    _patch_runner(monkeypatch, runner)

    res = queue(QueueIn(), actor="alice")

    assert res.status == ToolStatus.OK
    assert runner.calls == [[slurm_mod.SQUEUE, "--json"]]


def test_queue_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeQueueRunner(invalid=True)
    _patch_runner(monkeypatch, runner)

    res = queue(QueueIn(), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
