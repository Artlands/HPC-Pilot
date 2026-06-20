"""Tests for Phase C — SSH wrapping in _run().

Uses monkeypatch to stub subprocess.run and assert the exact argv _run()
produces when Cluster.ssh is set.
"""
from __future__ import annotations

import os
from unittest.mock import ANY, patch

import pytest


def _make_ssh_config(**kwargs) -> Any:
    """Build a minimal SSHConfig-like object."""
    from hpc_pilot.clusters import SSHConfig

    defaults = dict(host="cluster1.example.com", user="hpcop", key="~/.ssh/id_rsa")
    defaults.update(kwargs)
    return SSHConfig(**defaults)


def _make_cluster(ssh_config) -> Any:
    """Build a Cluster with the given SSHConfig."""
    from hpc_pilot.clusters import Cluster

    return Cluster(name="test-cluster", ssh=ssh_config)


class TestSshWrapping:
    """Verify the exact argv _run() produces with Cluster.ssh set."""

    def test_ssh_wrapping_basic(self):
        """Basic SSH wrapping: BatchMode, ConnectTimeout, key, user@host, remote args."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config()
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            _run(["/usr/bin/scontrol", "show", "node", "n01"], cluster=cluster)

        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd[:2] == ["ssh", "-o"]
        assert "BatchMode=yes" in cmd
        assert "ConnectTimeout=5" in cmd
        assert "-i" in cmd
        assert os.path.expanduser("~/.ssh/id_rsa") in cmd
        assert "hpcop@cluster1.example.com" in cmd
        sep_idx = cmd.index("--")
        assert cmd[sep_idx + 1:] == ["/usr/bin/scontrol", "show", "node", "n01"]
        assert any("StrictHostKeyChecking" in part for part in cmd)

    def test_ssh_wrapping_strict_host_key_checking_accept_new(self):
        """Default host_key_check=accept-new produces -o StrictHostKeyChecking=accept-new."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config()
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            _run(["scontrol", "version"], cluster=cluster)

        args, kwargs = mock_run.call_args
        cmd_str = " ".join(args[0])
        assert "StrictHostKeyChecking=accept-new" in cmd_str

    def test_ssh_wrapping_strict_host_key_checking_no(self):
        """host_key_check=no adds -o StrictHostKeyChecking=no."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config(host_key_check="no")
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            _run(["scontrol", "version"], cluster=cluster)

        args, kwargs = mock_run.call_args
        cmd_str = " ".join(args[0])
        assert "StrictHostKeyChecking=no" in cmd_str

    def test_ssh_wrapping_known_hosts_file(self):
        """known_hosts path adds -o UserKnownHostsFile=<path>."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config(known_hosts="/etc/hpc-pilot/known_hosts")
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            _run(["scontrol", "version"], cluster=cluster)

        args, kwargs = mock_run.call_args
        cmd_str = " ".join(args[0])
        assert "UserKnownHostsFile=/etc/hpc-pilot/known_hosts" in cmd_str

    def test_ssh_wrapping_control_path(self):
        """ControlPath options are inserted after the initial -o flags."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config(control_path="/tmp/hpc-pilot-%r@%h:%p")
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            _run(["scontrol", "version"], cluster=cluster)

        args, kwargs = mock_run.call_args
        cmd_str = " ".join(args[0])
        assert "ControlPath" in cmd_str
        assert "ControlMaster=auto" in cmd_str

    def test_ssh_remote_args_are_shquoted(self):
        """Remote command arguments are shell-quoted (shlex.quote)."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config()
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            _run(["echo", "$HOME; rm -rf /"], cluster=cluster)

        args, kwargs = mock_run.call_args
        cmd = args[0]
        # After --, args should be quoted
        sep_idx = cmd.index("--")
        remote_args = cmd[sep_idx + 1:]
        # shlex.quote wraps in single quotes
        assert all("'" in a or a == remote_args[0] for a in remote_args)

    def test_ssh_timeout_extension(self):
        """SSH wrapping extends the timeout by 5 seconds."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config()
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            _run(["scontrol", "version"], cluster=cluster, timeout=60)

        args, kwargs = mock_run.call_args
        # Base 60 + 5 SSH overhead
        assert kwargs["timeout"] == 65

    def test_no_ssh_when_cluster_has_no_ssh(self):
        """Without Cluster.ssh, _run() executes the command directly."""
        from hpc_pilot.tools._run import _run
        from hpc_pilot.clusters import Cluster

        cluster = Cluster(name="local")

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "direct\n"
            _run(["scontrol", "version"], cluster=cluster)

        args, kwargs = mock_run.call_args
        cmd = args[0]
        assert cmd == ["scontrol", "version"]
        assert "ssh" not in cmd

    def test_dry_run_returns_quoted_command(self):
        """dry_run=True returns the DRY-RUN message without executing."""
        from hpc_pilot.tools._run import _run

        ssh = _make_ssh_config()
        cluster = _make_cluster(ssh)

        with patch("hpc_pilot.tools._run.subprocess.run") as mock_run:
            result = _run(["scontrol", "version"], cluster=cluster, dry_run=True)

        mock_run.assert_not_called()
        assert result.startswith("DRY-RUN:")


