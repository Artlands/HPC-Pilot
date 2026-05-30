from __future__ import annotations

import json

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import NodeStatusIn, node_status


class FakeStatusRunner:
    def __init__(
        self, *, nodes: list[dict[str, object]] | None = None, invalid: bool = False
    ) -> None:
        self.calls: list[list[str]] = []
        self.nodes = nodes if nodes is not None else []
        self.invalid = invalid

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        stdout = "{not-json" if self.invalid else json.dumps({"nodes": self.nodes})
        return CommandResult(rc=0, stdout=stdout, stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeStatusRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_node_status_reads_single_node_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeStatusRunner(
        nodes=[
            {
                "name": "gpu01",
                "state": ["DRAIN"],
                "reason": "maintenance",
                "gres": "gpu:a100:8",
            }
        ]
    )
    _patch_runner(monkeypatch, runner)

    res = node_status(
        NodeStatusIn(node="gpu01", reconcile_state=False),
        actor="alice",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {
        "nodes": [
            {
                "node": "gpu01",
                "state": "DRAIN",
                "reason": "maintenance",
                "features": None,
                "gres": "gpu:a100:8",
            }
        ]
    }
    assert runner.calls == [[slurm_mod.SCONTROL, "show", "node", "gpu01", "--json"]]


def test_node_status_reads_all_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeStatusRunner(
        nodes=[
            {"name": "cpu01", "state": ["IDLE"], "reason": ""},
            {"name": "gpu01", "state": ["DOWN"], "reason": "hardware"},
        ]
    )
    _patch_runner(monkeypatch, runner)

    res = node_status(NodeStatusIn(reconcile_state=False), actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data and len(res.data["nodes"]) == 2
    assert runner.calls == [[slurm_mod.SCONTROL, "show", "node", "--json"]]


def test_node_status_missing_node(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeStatusRunner(nodes=[])
    _patch_runner(monkeypatch, runner)

    res = node_status(NodeStatusIn(node="ghost", reconcile_state=False), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "not_found"


def test_node_status_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeStatusRunner(invalid=True)
    _patch_runner(monkeypatch, runner)

    res = node_status(NodeStatusIn(reconcile_state=False), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
