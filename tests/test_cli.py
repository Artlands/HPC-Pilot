"""Tests for HPC Pilot CLI module."""
from __future__ import annotations

import argparse
import os
import sys
from io import StringIO
from unittest.mock import MagicMock, Mock, patch

import pytest

from hpc_pilot.cli import main


class TestHomeDirFunctions:
    """Tests for home_dir, config_file, ensure_home (canonical names)."""

    def test_home_dir_default(self):
        from hpc_pilot.cli import home_dir

        if "HPC_PILOT_HOME" in os.environ:
            del os.environ["HPC_PILOT_HOME"]

        assert home_dir() == os.path.expanduser("~/.hpc-pilot")

    def test_home_dir_env_var(self):
        from hpc_pilot.cli import home_dir

        test_path = "/test/path/hpc-pilot"
        os.environ["HPC_PILOT_HOME"] = test_path
        try:
            assert home_dir() == test_path
        finally:
            del os.environ["HPC_PILOT_HOME"]

    def test_config_file(self):
        from hpc_pilot.cli import config_file

        with patch("hpc_pilot.cli.home_dir", return_value="/test/hpc-pilot"):
            assert config_file() == "/test/hpc-pilot/config.yaml"

    @patch("hpc_pilot.paths.os.makedirs")
    @patch("hpc_pilot.paths.get_home", return_value="/test/hpc-pilot")
    def test_ensure_home(self, mock_get_home, mock_makedirs):
        from hpc_pilot.cli import ensure_home

        result = ensure_home()

        assert result == "/test/hpc-pilot"
        assert mock_makedirs.call_count == 4  # home + 3 subdirs


class TestDeprecatedShims:
    """Deprecated shims still work but emit DeprecationWarning."""

    def test_get_hermes_home_warns(self):
        from hpc_pilot.cli import get_hermes_home

        with pytest.warns(DeprecationWarning, match="get_hermes_home"):
            result = get_hermes_home()
        assert result == os.path.expanduser("~/.hpc-pilot")

    def test_get_config_path_warns(self):
        from hpc_pilot.cli import get_config_path

        with pytest.warns(DeprecationWarning, match="get_config_path"):
            result = get_config_path()
        assert result.endswith("config.yaml")

    def test_ensure_home_dir_warns(self):
        from hpc_pilot.cli import ensure_home_dir

        with patch("hpc_pilot.paths.os.makedirs"), \
             patch("hpc_pilot.paths.get_home", return_value="/test/hpc-pilot"):
            with pytest.warns(DeprecationWarning, match="ensure_home_dir"):
                result = ensure_home_dir()
        assert result == "/test/hpc-pilot"


class TestGetHermesHome:
    """Legacy class name kept so existing external tests that import it don't break."""

    def test_get_hermes_home_default(self):
        from hpc_pilot.cli import home_dir

        if "HPC_PILOT_HOME" in os.environ:
            del os.environ["HPC_PILOT_HOME"]

        assert home_dir() == os.path.expanduser("~/.hpc-pilot")

    def test_get_hermes_home_env_var(self):
        from hpc_pilot.cli import home_dir

        test_path = "/test/path/hpc-pilot"
        os.environ["HPC_PILOT_HOME"] = test_path
        try:
            assert home_dir() == test_path
        finally:
            del os.environ["HPC_PILOT_HOME"]


class TestGetConfigPath:
    """Legacy class name — delegates to config_file."""

    def test_get_config_path(self):
        from hpc_pilot.cli import config_file

        with patch("hpc_pilot.cli.home_dir", return_value="/test/hpc-pilot"):
            assert config_file() == "/test/hpc-pilot/config.yaml"


class TestEnsureHomeDir:
    """Legacy class name — delegates to ensure_home."""

    @patch("hpc_pilot.paths.os.makedirs")
    @patch("hpc_pilot.paths.get_home", return_value="/test/hpc-pilot")
    def test_ensure_home_dir(self, mock_get_home, mock_makedirs):
        from hpc_pilot.cli import ensure_home

        result = ensure_home()

        assert result == "/test/hpc-pilot"
        assert mock_makedirs.call_count == 4  # home + 3 subdirs


class TestNodesCommand:
    """Tests for nodes_command."""

    def test_nodes_command_with_node(self):
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="node01")
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="Node status info") as mock_invoke:
            result = nodes_command(args)

        assert result == 0
        mock_invoke.assert_called_once()
        assert mock_invoke.call_args[0][0] == "hpc_slurm_node_status"

    def test_nodes_command_without_node(self):
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="")
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="All nodes"):
            result = nodes_command(args)

        assert result == 0

    def test_nodes_command_tool_error(self):
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="node01")
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", side_effect=RuntimeError("Connection failed")):
            result = nodes_command(args)

        assert result == 1

    def test_nodes_command_invalid_name(self):
        """A node name with shell-special chars is rejected with exit code 2."""
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="--help")
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", side_effect=ValueError("Invalid node name")):
            result = nodes_command(args)

        assert result == 2


class TestQueueCommand:
    """Tests for queue_command."""

    def test_queue_command_with_filters(self):
        from hpc_pilot.cli import queue_command

        args = argparse.Namespace(user="alice", partition="gpu")
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="Queue status"):
            result = queue_command(args)

        assert result == 0

    def test_queue_command_no_filters(self):
        from hpc_pilot.cli import queue_command

        args = argparse.Namespace(user=None, partition=None)
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="Queue status"):
            result = queue_command(args)

        assert result == 0


class TestHealthCommand:
    """Tests for health_command."""

    def test_health_command_success(self):
        from hpc_pilot.cli import health_command

        args = argparse.Namespace()
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value='{"overall": "healthy"}'):
            result = health_command(args)

        assert result == 0


class TestQosCommand:
    """Tests for qos_command — gate behind --apply."""

    def test_qos_default_is_dry_run(self):
        """Without --apply, the command shows DRY-RUN output and does NOT call sacctmgr."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=False, yes=False)
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.cli.get_role", return_value=Role.ADMIN), \
             patch("hpc_pilot.dispatch.invoke", return_value="DRY-RUN: sacctmgr ...") as mock_invoke:
            result = qos_command(args)

        assert result == 0
        mock_invoke.assert_called_once()
        assert mock_invoke.call_args[1].get("dry_run") is True

    def test_qos_apply_with_yes(self):
        """With --apply --yes, sacctmgr is called without prompt."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=True, yes=True)
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.cli.get_role", return_value=Role.ADMIN), \
             patch("hpc_pilot.dispatch.invoke", return_value="Modified") as mock_invoke:
            result = qos_command(args)

        assert result == 0
        mock_invoke.assert_called()

    def test_qos_rbac_viewer_denied(self):
        """A VIEWER is denied access to qos_modify (ADMIN required)."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=True, yes=True)
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.cli.get_role", return_value=Role.VIEWER), \
             patch("hpc_pilot.dispatch.invoke", side_effect=PermissionError("requires role 'admin'")):
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
        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="NODE LIST"):
            result = main(["warewulf"])
        # May fail if wwctl is absent, but the command must not raise SystemExit
        assert isinstance(result, int)

    def test_main_werewulf_not_registered(self):
        """'werewulf' (old typo) is NOT a registered subcommand."""
        with pytest.raises(SystemExit) as exc_info:
            main(["werewulf"])
        assert exc_info.value.code == 2

    def test_main_gateway_setup_delegates(self):
        """hpc-pilot gateway --setup delegates to gateway.main with ['--setup']."""
        with patch("hpc_pilot.gateway.main") as mock_gw:
            mock_gw.return_value = 0
            result = main(["gateway", "--setup"])
        mock_gw.assert_called_once_with(["--setup"])
        assert result == 0

    def test_main_gateway_status_delegates(self):
        """hpc-pilot gateway --status delegates to gateway.main with ['--status']."""
        with patch("hpc_pilot.gateway.main") as mock_gw:
            mock_gw.return_value = 0
            result = main(["gateway", "--status"])
        mock_gw.assert_called_once_with(["--status"])
        assert result == 0

    def test_main_gateway_bare_starts(self):
        """hpc-pilot gateway with no flags defaults to --start."""
        with patch("hpc_pilot.gateway.main") as mock_gw:
            mock_gw.return_value = 0
            result = main(["gateway"])
        mock_gw.assert_called_once_with(["--start"])
        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
