"""Tests for HPC Pilot CLI module."""
from __future__ import annotations

import argparse
import os
import sys
from io import StringIO
from unittest.mock import patch

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

        with patch("hpc_pilot._cli_base.home_dir", return_value="/test/hpc-pilot"):
            assert config_file() == "/test/hpc-pilot/config.yaml"

    @patch("hpc_pilot.paths.os.makedirs")
    @patch("hpc_pilot.paths.get_home", return_value="/test/hpc-pilot")
    def test_ensure_home(self, mock_get_home, mock_makedirs):
        from hpc_pilot.cli import ensure_home

        result = ensure_home()

        assert result == "/test/hpc-pilot"
        assert mock_makedirs.call_count == 7  # home + 6 subdirs


class TestHomeDir:
    """Tests for ``home_dir()``."""

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


class TestConfigFile:
    """Tests for ``config_file()``."""

    def test_get_config_path(self):
        from hpc_pilot.cli import config_file

        with patch("hpc_pilot._cli_base.home_dir", return_value="/test/hpc-pilot"):
            assert config_file() == "/test/hpc-pilot/config.yaml"


class TestEnsureHome:
    """Tests for ``ensure_home()``."""

    @patch("hpc_pilot.paths.os.makedirs")
    @patch("hpc_pilot.paths.get_home", return_value="/test/hpc-pilot")
    def test_ensure_home_dir(self, mock_get_home, mock_makedirs):
        from hpc_pilot.cli import ensure_home

        result = ensure_home()

        assert result == "/test/hpc-pilot"
        assert mock_makedirs.call_count == 7  # home + 6 subdirs


class TestNodesCommand:
    """Tests for nodes_command."""

    def test_nodes_command_with_node(self):
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="node01")
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="Node status info") as mock_invoke:
            result = nodes_command(args)

        assert result == 0
        mock_invoke.assert_called_once()
        assert mock_invoke.call_args[0][0] == "hpc_slurm_node_status"

    def test_nodes_command_without_node(self):
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="")
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="All nodes"):
            result = nodes_command(args)

        assert result == 0

    def test_nodes_command_tool_error(self):
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="node01")
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", side_effect=RuntimeError("Connection failed")):
            result = nodes_command(args)

        assert result == 1

    def test_nodes_command_invalid_name(self):
        """A node name with shell-special chars is rejected with exit code 2."""
        from hpc_pilot.cli import nodes_command

        args = argparse.Namespace(node="--help")
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", side_effect=ValueError("Invalid node name")):
            result = nodes_command(args)

        assert result == 2


class TestQueueCommand:
    """Tests for queue_command."""

    def test_queue_command_with_filters(self):
        from hpc_pilot.cli import queue_command

        args = argparse.Namespace(user="alice", partition="gpu")
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="Queue status"):
            result = queue_command(args)

        assert result == 0

    def test_queue_command_no_filters(self):
        from hpc_pilot.cli import queue_command

        args = argparse.Namespace(user=None, partition=None)
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot.dispatch.invoke", return_value="Queue status"):
            result = queue_command(args)

        assert result == 0


class TestHealthCommand:
    """Tests for health_command."""

    def test_health_command_success(self):
        from hpc_pilot.cli import health_command

        args = argparse.Namespace()
        with patch("hpc_pilot._cli_system.ensure_home"), \
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
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot._cli_slurm.get_role", return_value=Role.ADMIN), \
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
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot._cli_slurm.get_role", return_value=Role.ADMIN), \
             patch("hpc_pilot.dispatch.invoke", return_value="Modified") as mock_invoke:
            result = qos_command(args)

        assert result == 0
        mock_invoke.assert_called()

    def test_qos_rbac_viewer_denied(self):
        """A VIEWER is denied access to qos_modify (ADMIN required)."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=True, yes=True)
        with patch("hpc_pilot._cli_slurm.ensure_home"), \
             patch("hpc_pilot._cli_slurm.get_role", return_value=Role.VIEWER), \
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


class TestConfigCommand:
    """Tests for config_command."""

    @patch("subprocess.run")
    def test_config_set_success(self, mock_run):
        from hpc_pilot.cli import config_command

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "✓ Set providers.local.name = Local\n"

        args = argparse.Namespace(action="set", key="providers.local.name", value="Local")
        with patch("hpc_pilot.agent._find_hermes", return_value="/usr/local/bin/hermes"):
            result = config_command(args)

        assert result == 0
        mock_run.assert_called_once_with(
            ["/usr/local/bin/hermes", "config", "set", "providers.local.name", "Local"],
            capture_output=True, text=True,
        )

    @patch("subprocess.run")
    def test_config_set_failure(self, mock_run):
        from hpc_pilot.cli import config_command

        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "Unknown key"

        args = argparse.Namespace(action="set", key="invalid.key", value="x")
        with patch("hpc_pilot.agent._find_hermes", return_value="hermes"):
            result = config_command(args)

        assert result == 1

    @patch("subprocess.run")
    def test_config_list(self, mock_run):
        from hpc_pilot.cli import config_command

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Config output\n"

        args = argparse.Namespace(action="list", key=None, value=None)
        with patch("hpc_pilot.agent._find_hermes", return_value="hermes"):
            result = config_command(args)

        assert result == 0
        mock_run.assert_called_once_with(
            ["hermes", "config", "show"],
            capture_output=True, text=True,
        )

    @patch("subprocess.run")
    def test_config_show(self, mock_run):
        from hpc_pilot.cli import config_command

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Config output\n"

        args = argparse.Namespace(action="show", key=None, value=None)
        with patch("hpc_pilot.agent._find_hermes", return_value="hermes"):
            result = config_command(args)

        assert result == 0

    @patch("subprocess.run")
    def test_config_get(self, mock_run):
        from hpc_pilot.cli import config_command

        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "Config output\n"

        args = argparse.Namespace(action="get", key="providers", value=None)
        with patch("hpc_pilot.agent._find_hermes", return_value="hermes"):
            result = config_command(args)

        assert result == 0

    def test_config_set_missing_key(self):
        from hpc_pilot.cli import config_command

        args = argparse.Namespace(action="set", key=None, value="x")
        with patch("hpc_pilot.agent._find_hermes", return_value="hermes"):
            result = config_command(args)

        # Should print usage and return 0
        assert result == 0

    def test_config_no_action_shows_usage(self):
        from hpc_pilot.cli import config_command

        args = argparse.Namespace(action=None, key=None, value=None)
        with patch("hpc_pilot.agent._find_hermes", return_value="hermes"):
            result = config_command(args)

        assert result == 0

    def test_main_config_set(self):
        """hpc-pilot config set ... via main()."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "ok\n"
            result = main(["config", "set", "providers.test.key", "val"])

        assert result == 0

    def test_main_config_list(self):
        """hpc-pilot config list via main()."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = "Config:\n  ...\n"
            result = main(["config", "list"])

        assert result == 0

    def test_config_reload(self):
        """config reload invalidates caches and returns 0."""
        import argparse
        from hpc_pilot.cli import config_command

        args = argparse.Namespace(action="reload", key=None, value=None)
        with patch("hpc_pilot.agent._find_hermes", return_value="hermes"):
            result = config_command(args)

        assert result == 0


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
        with patch("hpc_pilot._cli_system.ensure_home"), \
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

    def test_main_gateway_status_local(self):
        """hpc-pilot gateway --status now handled locally (not delegating to gateway.main)."""
        with patch("hpc_pilot._cli_gateway._gateway_status", return_value=0) as mock_status:
            result = main(["gateway", "--status"])
        mock_status.assert_called_once()
        assert result == 0

    def test_main_gateway_bare_starts_local(self):
        """hpc-pilot gateway with no flags calls _gateway_start."""
        with patch("hpc_pilot._cli_gateway._gateway_start", return_value=0) as mock_start:
            result = main(["gateway"])
        mock_start.assert_called_once()
        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
