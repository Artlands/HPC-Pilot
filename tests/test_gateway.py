"""Tests for HPC Pilot gateway module."""
from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest

from hpc_pilot.gateway import main


class TestGatewayHomeFunctions:
    """Tests for gateway home directory helpers (delegating to paths module)."""

    def test_get_home_default(self):
        from hpc_pilot.gateway import get_home

        if "HPC_PILOT_HOME" in os.environ:
            del os.environ["HPC_PILOT_HOME"]

        assert get_home() == os.path.expanduser("~/.hpc-pilot")

    def test_get_home_env_var(self):
        from hpc_pilot.gateway import get_home

        test_path = "/test/hpc-pilot"
        os.environ["HPC_PILOT_HOME"] = test_path
        try:
            assert get_home() == test_path
        finally:
            del os.environ["HPC_PILOT_HOME"]

    @patch("hpc_pilot.paths.os.makedirs")
    @patch("hpc_pilot.paths.get_home", return_value="/test/hpc-pilot")
    def test_init_home(self, mock_get_home, mock_makedirs):
        """init_home (alias for ensure_layout) creates 4 directories."""
        from hpc_pilot.gateway import init_home

        result = init_home()

        assert result == "/test/hpc-pilot"
        assert mock_makedirs.call_count == 4  # home + 3 subdirs


class TestGatewayMain:
    """Tests for gateway main function."""

    @patch("hpc_pilot.gateway.init_home")
    @patch("hpc_pilot.gateway.init_config")
    def test_gateway_setup(self, mock_init_config, mock_init_home):
        result = main(["--setup"])
        assert result == 0

    @patch("hpc_pilot.gateway.init_home")
    @patch("hpc_pilot.gateway.init_config")
    def test_gateway_start_returns_1(self, mock_init_config, mock_init_home):
        """Gateway --start is not yet implemented → returns 1."""
        result = main(["--start"])
        assert result == 1

    @patch("hpc_pilot.gateway.init_home")
    @patch("hpc_pilot.gateway.init_config")
    def test_gateway_status(self, mock_init_config, mock_init_home):
        result = main(["--status"])
        assert result == 0

    @patch("hpc_pilot.gateway.init_home")
    @patch("hpc_pilot.gateway.init_config")
    def test_gateway_stop(self, mock_init_config, mock_init_home):
        result = main(["--stop"])
        assert result == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
