"""Tests for HPC Pilot CLI module."""
from __future__ import annotations

import argparse
import os
import sys
from io import StringIO
from unittest.mock import MagicMock, Mock, patch

import pytest

from hpc_pilot.cli import main


class TestGetHermesHome:
    """Tests for get_hermes_home (backward-compat shim in cli)."""

    def test_get_hermes_home_default(self):
        from hpc_pilot.cli import get_hermes_home

        if "HPC_PILOT_HOME" in os.environ:
            del os.environ["HPC_PILOT_HOME"]

        result = get_hermes_home()
        assert result == os.path.expanduser("~/.hpc-pilot")

    def test_get_hermes_home_env_var(self):
        from hpc_pilot.cli import get_hermes_home

        test_path = "/test/path/hpc-pilot"
        os.environ["HPC_PILOT_HOME"] = test_path
        try:
            assert get_hermes_home() == test_path
        finally:
            del os.environ["HPC_PILOT_HOME"]


class TestGetConfigPath:
    """Tests for get_config_path (backward-compat shim in cli)."""

    def test_get_config_path(self):
        from hpc_pilot.cli import get_config_path

        with patch("hpc_pilot.cli.get_hermes_home", return_value="/test/hpc-pilot"):
            result = get_config_path()
            assert result == "/test/hpc-pilot/config.yaml"


class TestEnsureHomeDir:
    """Tests for ensure_home_dir (delegates to paths.ensure_layout)."""

    @patch("hpc_pilot.paths.os.makedirs")
    @patch("hpc_pilot.paths.get_home", return_value="/test/hpc-pilot")
    def test_ensure_home_dir(self, mock_get_home, mock_makedirs):
        from hpc_pilot.cli import ensure_home_dir

        result = ensure_home_dir()

        assert result == "/test/hpc-pilot"
        assert mock_makedirs.call_count == 4  # home + 3 subdirs


class TestNodesCommand:
    """Tests for nodes_command."""

    @patch("hpc_pilot.cli.ensure_home_dir")
    @patch("hpc_pilot.cli.audit_tool")
    def test_nodes_command_with_node(self, mock_audit, mock_ensure):
        from hpc_pilot.cli import nodes_command
        from hpc_pilot import tools

        mock_audit.return_value.__enter__ = Mock(return_value=None)
        mock_audit.return_value.__exit__ = Mock(return_value=False)

        args = argparse.Namespace(node="node01")
        with patch.object(tools, "hpc_slurm_node_status", return_value="Node status info"):
            result = nodes_command(args)

        assert result == 0

    @patch("hpc_pilot.cli.ensure_home_dir")
    @patch("hpc_pilot.cli.audit_tool")
    def test_nodes_command_without_node(self, mock_audit, mock_ensure):
        from hpc_pilot.cli import nodes_command
        from hpc_pilot import tools

        mock_audit.return_value.__enter__ = Mock(return_value=None)
        mock_audit.return_value.__exit__ = Mock(return_value=False)

        args = argparse.Namespace(node="")
        with patch.object(tools, "hpc_slurm_node_status", return_value="All nodes"):
            result = nodes_command(args)

        assert result == 0

    @patch("hpc_pilot.cli.ensure_home_dir")
    @patch("hpc_pilot.cli.audit_tool")
    def test_nodes_command_tool_error(self, mock_audit, mock_ensure):
        from hpc_pilot.cli import nodes_command
        from hpc_pilot import tools

        # audit_tool must re-raise when the tool raises
        from contextlib import contextmanager

        @contextmanager
        def real_audit(*a, **kw):
            try:
                yield
            except Exception:
                raise

        mock_audit.side_effect = real_audit

        args = argparse.Namespace(node="node01")
        with patch.object(tools, "hpc_slurm_node_status", side_effect=RuntimeError("Connection failed")):
            result = nodes_command(args)

        assert result == 1

    def test_nodes_command_invalid_name(self):
        """A node name with shell-special chars is rejected with exit code 2."""
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="--help")
        with patch("hpc_pilot.cli.ensure_home_dir"):
            result = nodes_command(args)

        assert result == 2


class TestQueueCommand:
    """Tests for queue_command."""

    @patch("hpc_pilot.cli.ensure_home_dir")
    @patch("hpc_pilot.cli.audit_tool")
    def test_queue_command_with_filters(self, mock_audit, mock_ensure):
        from hpc_pilot.cli import queue_command
        from hpc_pilot import tools

        mock_audit.return_value.__enter__ = Mock(return_value=None)
        mock_audit.return_value.__exit__ = Mock(return_value=False)

        args = argparse.Namespace(user="alice", partition="gpu")
        with patch.object(tools, "hpc_slurm_queue", return_value="Queue status"):
            result = queue_command(args)

        assert result == 0

    @patch("hpc_pilot.cli.ensure_home_dir")
    @patch("hpc_pilot.cli.audit_tool")
    def test_queue_command_no_filters(self, mock_audit, mock_ensure):
        from hpc_pilot.cli import queue_command
        from hpc_pilot import tools

        mock_audit.return_value.__enter__ = Mock(return_value=None)
        mock_audit.return_value.__exit__ = Mock(return_value=False)

        args = argparse.Namespace(user=None, partition=None)
        with patch.object(tools, "hpc_slurm_queue", return_value="Queue status"):
            result = queue_command(args)

        assert result == 0


class TestHealthCommand:
    """Tests for health_command."""

    @patch("hpc_pilot.cli.ensure_home_dir")
    @patch("hpc_pilot.cli.audit_tool")
    def test_health_command_success(self, mock_audit, mock_ensure):
        from hpc_pilot.cli import health_command
        from hpc_pilot import tools

        mock_audit.return_value.__enter__ = Mock(return_value=None)
        mock_audit.return_value.__exit__ = Mock(return_value=False)

        args = argparse.Namespace()
        with patch.object(tools, "hpc_cluster_health_check", return_value={"overall": "healthy"}):
            result = health_command(args)

        assert result == 0


class TestQosCommand:
    """Tests for qos_command — gate behind --apply."""

    @patch("hpc_pilot.cli.ensure_home_dir")
    def test_qos_default_is_dry_run(self, mock_ensure):
        """Without --apply, the command shows DRY-RUN output and does NOT call sacctmgr."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot import tools
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=False, yes=False)
        with patch("hpc_pilot.cli.get_role", return_value=Role.ADMIN), \
             patch.object(tools, "hpc_slurm_qos_modify", return_value="DRY-RUN: sacctmgr ...") as mock_tool:
            result = qos_command(args)

        assert result == 0
        mock_tool.assert_called_once_with("gpu", 60, dry_run=True)

    @patch("hpc_pilot.cli.ensure_home_dir")
    @patch("hpc_pilot.cli.audit_tool")
    def test_qos_apply_with_yes(self, mock_audit, mock_ensure):
        """With --apply --yes, sacctmgr is called without prompt."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot import tools
        from hpc_pilot.rbac import Role

        mock_audit.return_value.__enter__ = Mock(return_value=None)
        mock_audit.return_value.__exit__ = Mock(return_value=False)

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=True, yes=True)
        with patch("hpc_pilot.cli.get_role", return_value=Role.ADMIN), \
             patch.object(tools, "hpc_slurm_qos_modify", return_value="Modified") as mock_tool:
            result = qos_command(args)

        assert result == 0
        mock_tool.assert_called_once_with("gpu", 60, dry_run=False)

    @patch("hpc_pilot.cli.ensure_home_dir")
    def test_qos_rbac_viewer_denied(self, mock_ensure):
        """A VIEWER is denied access to qos_modify (ADMIN required)."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=True, yes=True)
        with patch("hpc_pilot.cli.get_role", return_value=Role.VIEWER):
            result = qos_command(args)

        assert result == 1


class TestVersionCommand:
    """Tests for version_command."""

    def test_version_command_success(self):
        from hpc_pilot.cli import version_command

        args = argparse.Namespace()
        old_stdout, sys.stdout = sys.stdout, StringIO()
        try:
            result = version_command(args)
            output = sys.stdout.getvalue()
        finally:
            sys.stdout = old_stdout

        assert result == 0
        assert "HPC Pilot" in output
        assert "Python" in output


class TestMain:
    """Tests for main entry point."""

    def test_main_no_command_returns_1(self):
        """Default command is chat, which is not yet implemented → exit 1."""
        result = main([])
        assert result == 1

    def test_main_version_command(self):
        result = main(["version"])
        assert result == 0

    def test_main_help(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    def test_main_warewulf_command_registered(self):
        """warewulf subcommand is registered (not werewulf)."""
        with patch("hpc_pilot.cli.ensure_home_dir"), \
             patch("hpc_pilot.cli.audit_tool") as mock_audit:
            from hpc_pilot import tools
            mock_audit.return_value.__enter__ = Mock(return_value=None)
            mock_audit.return_value.__exit__ = Mock(return_value=False)
            with patch.object(tools, "hpc_warewulf_node_status", return_value="NODE LIST"):
                result = main(["warewulf"])
        # May fail if wwctl is absent, but the command must not raise SystemExit
        assert isinstance(result, int)

    def test_main_werewulf_not_registered(self):
        """'werewulf' (old typo) is NOT a registered subcommand."""
        with pytest.raises(SystemExit) as exc_info:
            main(["werewulf"])
        assert exc_info.value.code == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
