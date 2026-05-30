"""Tests for Warewulf bootstrap tools (spec 09)."""

from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.warewulf_bootstrap as wb_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.warewulf_bootstrap import (
    ConfigureDhcpIn,
    ConfigureNfsIn,
    ConfigureTftpIn,
    ServerStatusIn,
    configure_dhcp,
    configure_nfs,
    configure_tftp,
    server_status,
)

POLICY_DIR = Path(__file__).resolve().parents[2] / "config_repo" / "policy"


class FakeBootstrapRunner:
    def __init__(self, stdout: str = "", rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.stdout = stdout
        self.rc = rc

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(list(spec.argv))
        return CommandResult(rc=self.rc, stdout=self.stdout, stderr="", duration_s=0.01)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def _patch(monkeypatch: pytest.MonkeyPatch, runner: FakeBootstrapRunner) -> None:
    monkeypatch.setattr(wb_mod, "run_command", runner)


# --- server_status ---


def test_server_status_wwctl_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _: None)
    runner = FakeBootstrapRunner()
    _patch(monkeypatch, runner)

    res = server_status(ServerStatusIn(), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "precondition"
    assert runner.calls == []


def test_server_status_running(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/wwctl")
    runner = FakeBootstrapRunner(
        stdout="Warewulf v4.5.2\ndhcp configured\ntftp configured\nrunning\n",
        rc=0,
    )
    _patch(monkeypatch, runner)

    res = server_status(ServerStatusIn(), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.OK
    assert res.data is not None
    assert res.data["installed"] is True
    assert res.data["running"] is True
    assert res.data["version"] == "v4.5.2"
    assert runner.calls[0][1:3] == ["server", "status"]


def test_server_status_operator_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/wwctl")
    runner = FakeBootstrapRunner(stdout="running\n", rc=0)
    _patch(monkeypatch, runner)

    res = server_status(ServerStatusIn(), actor="alice", actor_role=Role.OPERATOR)
    assert res.status == ToolStatus.OK


# --- configure_dhcp ---


def test_configure_dhcp_dry_run(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBootstrapRunner()
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0",
        range_start="10.1.0.100",
        range_end="10.1.0.254",
        dry_run=True,
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert runner.calls == []


def test_configure_dhcp_always_needs_approval(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    """configure_dhcp policy rule forces require_approval."""
    runner = FakeBootstrapRunner(rc=0)
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0",
        range_start="10.1.0.100",
        range_end="10.1.0.254",
        dry_run=False,
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    # Policy forces approval for DHCP — tool should report needs_approval
    assert res.status == ToolStatus.NEEDS_APPROVAL
    assert runner.calls == []


def test_configure_dhcp_command_failure_when_no_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a policy, HIGH risk still goes through gate as requires_approval."""
    runner = FakeBootstrapRunner(rc=1)
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0",
        range_start="10.1.0.100",
        range_end="10.1.0.254",
        dry_run=True,
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN)

    assert res.status == ToolStatus.DRY_RUN


def test_configure_dhcp_diff_shows_range(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBootstrapRunner()
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0",
        range_start="10.1.0.100",
        range_end="10.1.0.254",
        dry_run=True,
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.diff is not None
    preview = " ".join(" ".join(c) for c in res.diff.commands_preview)
    assert "10.1.0.100" in preview


# --- configure_tftp ---


def test_configure_tftp_dry_run(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBootstrapRunner()
    _patch(monkeypatch, runner)

    res = configure_tftp(
        ConfigureTftpIn(dry_run=True), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.DRY_RUN
    assert runner.calls == []


def test_configure_tftp_applies_with_policy(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBootstrapRunner(rc=0)
    _patch(monkeypatch, runner)

    res = configure_tftp(
        ConfigureTftpIn(interface="eth0", dry_run=False),
        actor="alice",
        actor_role=Role.ADMIN,
        policy=policy,
    )

    assert res.status == ToolStatus.OK
    assert any("tftp" in " ".join(c) for c in runner.calls)


def test_configure_tftp_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBootstrapRunner(rc=1)
    _patch(monkeypatch, runner)

    res = configure_tftp(
        ConfigureTftpIn(dry_run=False), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "command_failed"


# --- configure_nfs ---


def test_configure_nfs_dry_run(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBootstrapRunner()
    _patch(monkeypatch, runner)

    inp = ConfigureNfsIn(exports=["/home", "/scratch"], dry_run=True)
    res = configure_nfs(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.status == ToolStatus.DRY_RUN
    assert runner.calls == []


def test_configure_nfs_runs_per_export(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine, tmp_path: pytest.TempDir
) -> None:
    runner = FakeBootstrapRunner(rc=0)
    _patch(monkeypatch, runner)
    monkeypatch.setenv("HPC_CONFIG_REPO", str(tmp_path))

    inp = ConfigureNfsIn(exports=["/home", "/scratch", "/opt/spack"], dry_run=False)
    res = configure_nfs(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.status == ToolStatus.OK
    nfs_calls = [c for c in runner.calls if "nfs" in c]
    assert len(nfs_calls) == 3


def test_configure_nfs_failure_stops_early(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBootstrapRunner(rc=1)
    _patch(monkeypatch, runner)

    inp = ConfigureNfsIn(exports=["/home", "/scratch"], dry_run=False)
    res = configure_nfs(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "command_failed"
    assert len(runner.calls) == 1
