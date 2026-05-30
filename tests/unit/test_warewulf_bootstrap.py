"""Tests for Warewulf bootstrap tools (spec 09).

The configure_* tools edit a managed warewulf.conf in the config repo, then run
`wwctl configure <service>`. We patch wb_mod.run_command to fake `wwctl`; the config
repo git operations use the real runner against a tmp_path repo.
"""

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


class FakeWwctl:
    """Fakes `wwctl` only; git (configrepo) still uses the real runner."""

    def __init__(self, rc: int = 0, stdout: str = "") -> None:
        self.calls: list[list[str]] = []
        self.rc = rc
        self.stdout = stdout

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(list(spec.argv))
        return CommandResult(rc=self.rc, stdout=self.stdout, stderr="", duration_s=0.01)

    @property
    def configure_calls(self) -> list[list[str]]:
        return [c for c in self.calls if "configure" in c]


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


@pytest.fixture(autouse=True)
def config_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HPC_CONFIG_REPO", str(tmp_path / "config"))


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def _patch(monkeypatch: pytest.MonkeyPatch, runner: FakeWwctl) -> None:
    monkeypatch.setattr(wb_mod, "run_command", runner)


# --- server_status ---


def test_server_status_wwctl_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _: None)
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    res = server_status(ServerStatusIn(), actor="alice", actor_role=Role.VIEWER)

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "precondition"
    assert runner.calls == []


def test_server_status_running_parses_version(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil as _shutil

    monkeypatch.setattr(_shutil, "which", lambda _: "/usr/bin/wwctl")
    runner = FakeWwctl(stdout="Warewulf v4.5.2\ndhcp configured\nrunning\n")
    _patch(monkeypatch, runner)

    res = server_status(ServerStatusIn(), actor="alice", actor_role=Role.OPERATOR)

    assert res.status == ToolStatus.OK
    assert res.data is not None
    assert res.data["installed"] is True
    assert res.data["running"] is True
    assert res.data["version"] == "v4.5.2"
    assert runner.calls[0][1:3] == ["server", "status"]


# --- configure_dhcp (HIGH risk — always approval) ---


def test_configure_dhcp_dry_run(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0", range_start="192.168.122.100", range_end="192.168.122.200", dry_run=True
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert res.diff.config_diff is not None  # warewulf.conf would change
    assert runner.configure_calls == []


def test_configure_dhcp_command_preview_uses_no_flags(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0", range_start="192.168.122.100", range_end="192.168.122.200", dry_run=True
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.diff is not None
    assert res.diff.commands_preview == [[wb_mod.WWCTL, "configure", "dhcp"]]


def test_configure_dhcp_always_needs_approval(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0", range_start="192.168.122.100", range_end="192.168.122.200", dry_run=False
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.status == ToolStatus.NEEDS_APPROVAL
    assert runner.configure_calls == []


def test_configure_dhcp_high_risk_approval_without_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """HIGH risk forces approval even with no policy engine."""
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    inp = ConfigureDhcpIn(
        interface="eth0", range_start="192.168.122.100", range_end="192.168.122.200", dry_run=False
    )
    res = configure_dhcp(inp, actor="alice", actor_role=Role.ADMIN)

    assert res.status == ToolStatus.NEEDS_APPROVAL


# --- configure_tftp (MEDIUM — auto via policy) ---


def test_configure_tftp_dry_run(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    res = configure_tftp(
        ConfigureTftpIn(dry_run=True), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.DRY_RUN
    assert runner.configure_calls == []


def test_configure_tftp_applies(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeWwctl(rc=0)
    _patch(monkeypatch, runner)

    res = configure_tftp(
        ConfigureTftpIn(dry_run=False), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.OK
    assert res.config_commit is not None
    assert runner.configure_calls == [[wb_mod.WWCTL, "configure", "tftp"]]


def test_configure_tftp_idempotent_second_run_is_noop(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeWwctl(rc=0)
    _patch(monkeypatch, runner)

    first = configure_tftp(
        ConfigureTftpIn(dry_run=False), actor="alice", actor_role=Role.ADMIN, policy=policy
    )
    assert first.status == ToolStatus.OK

    second = configure_tftp(
        ConfigureTftpIn(dry_run=False), actor="alice", actor_role=Role.ADMIN, policy=policy
    )
    assert second.status == ToolStatus.OK
    assert second.data == {"changed": False}
    # Only the first run invoked `wwctl configure tftp`.
    assert len(runner.configure_calls) == 1


def test_configure_tftp_command_failure(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeWwctl(rc=1)
    _patch(monkeypatch, runner)

    res = configure_tftp(
        ConfigureTftpIn(dry_run=False), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "command_failed"


# --- configure_nfs (MEDIUM — auto via policy) ---


def test_configure_nfs_dry_run(monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine) -> None:
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    res = configure_nfs(
        ConfigureNfsIn(exports=["/home", "/scratch"], dry_run=True),
        actor="alice",
        actor_role=Role.ADMIN,
        policy=policy,
    )

    assert res.status == ToolStatus.DRY_RUN
    assert runner.configure_calls == []


def test_configure_nfs_single_configure_call(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeWwctl(rc=0)
    _patch(monkeypatch, runner)

    res = configure_nfs(
        ConfigureNfsIn(exports=["/home", "/scratch", "/opt/spack"], dry_run=False),
        actor="alice",
        actor_role=Role.ADMIN,
        policy=policy,
    )

    assert res.status == ToolStatus.OK
    # All exports go into warewulf.conf; `wwctl configure nfs` runs exactly once.
    assert runner.configure_calls == [[wb_mod.WWCTL, "configure", "nfs"]]
    assert res.config_commit is not None


def test_configure_nfs_command_failure(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeWwctl(rc=1)
    _patch(monkeypatch, runner)

    res = configure_nfs(
        ConfigureNfsIn(exports=["/home"], dry_run=False),
        actor="alice",
        actor_role=Role.ADMIN,
        policy=policy,
    )

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "command_failed"


def test_configure_nfs_blast_radius_matches_export_count(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeWwctl()
    _patch(monkeypatch, runner)

    res = configure_nfs(
        ConfigureNfsIn(exports=["/home", "/scratch", "/opt/spack"], dry_run=True),
        actor="alice",
        actor_role=Role.ADMIN,
        policy=policy,
    )

    assert res.diff is not None
    assert res.diff.blast_radius == 3
