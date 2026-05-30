"""Tests for warewulf.node_status tool."""

from __future__ import annotations

import pytest

import hpc_agent.tools.warewulf as ww_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.warewulf import WwNodeStatusIn, ww_node_status


class FakeWwRunner:
    def __init__(self, stdout: str = "", rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.stdout = stdout
        self.rc = rc

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        return CommandResult(rc=self.rc, stdout=self.stdout, stderr="", duration_s=0.01)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch(monkeypatch: pytest.MonkeyPatch, runner: FakeWwRunner) -> None:
    monkeypatch.setattr(ww_mod, "run_command", runner)


_NODE_OUTPUT = (
    "NODE      PROFILES      IMAGES              NETDEV  HWADDR              IPADDR\n"
    "cpu01     cpu-default   rockylinux9-cpu     eth0    aa:bb:cc:dd:ee:ff   10.1.0.101\n"
)


def test_node_status_found(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeWwRunner(stdout=_NODE_OUTPUT, rc=0)
    _patch(monkeypatch, runner)

    res = ww_node_status(WwNodeStatusIn(node="cpu01"), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.OK
    assert res.data is not None
    assert res.data["node"] == "cpu01"
    assert res.data.get("profile") == "cpu-default"
    assert runner.calls == [[ww_mod.WWCTL, "node", "show", "cpu01"]]


def test_node_status_not_found_rc_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeWwRunner(stdout="", rc=1)
    _patch(monkeypatch, runner)

    res = ww_node_status(WwNodeStatusIn(node="ghost"), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "not_found"


def test_node_status_not_found_empty_output(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeWwRunner(stdout="NODE      PROFILES\n", rc=0)
    _patch(monkeypatch, runner)

    res = ww_node_status(WwNodeStatusIn(node="ghost"), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "not_found"


def test_node_status_viewer_role_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeWwRunner(stdout=_NODE_OUTPUT, rc=0)
    _patch(monkeypatch, runner)

    res = ww_node_status(WwNodeStatusIn(node="cpu01"), actor="bob", actor_role=Role.VIEWER)
    assert res.status == ToolStatus.OK


def test_node_status_calls_correct_command(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeWwRunner(stdout=_NODE_OUTPUT, rc=0)
    _patch(monkeypatch, runner)

    ww_node_status(WwNodeStatusIn(node="gpu01"), actor="alice")

    assert runner.calls[0] == [ww_mod.WWCTL, "node", "show", "gpu01"]
