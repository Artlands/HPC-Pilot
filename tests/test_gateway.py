"""Tests for HPC Pilot gateway module."""
from __future__ import annotations

import os
from unittest.mock import Mock, patch

import pytest

from hpc_pilot.gateway import main


class TestGatewayHomeFunctions:
    """Tests for gateway home directory helpers (delegating to paths module)."""

    def test_get_home_default(self):
        from hpc_pilot.paths import get_home

        if "HPC_PILOT_HOME" in os.environ:
            del os.environ["HPC_PILOT_HOME"]

        assert get_home() == os.path.expanduser("~/.hpc-pilot")

    def test_get_home_env_var(self):
        from hpc_pilot.paths import get_home

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
    def test_gateway_status(self, mock_init_config, mock_init_home):
        result = main(["--status"])
        assert result == 0

    @patch("hpc_pilot.gateway.init_home")
    @patch("hpc_pilot.gateway.init_config")
    def test_gateway_stop(self, mock_init_config, mock_init_home):
        result = main(["--stop"])
        assert result == 0

    @patch("hpc_pilot.gateway.init_home")
    @patch("hpc_pilot.gateway.init_config")
    def test_gateway_role_flag_sets_env(self, mock_init_config, mock_init_home):
        """--role admin sets HPC_PILOT_ROLE before the gateway starts."""
        if "HPC_PILOT_ROLE" in os.environ:
            del os.environ["HPC_PILOT_ROLE"]
        try:
            with patch("hpc_pilot.gateway.asyncio.run") as mock_run:
                mock_run.return_value = 0
                main(["--start", "--role", "admin"])
            assert os.environ.get("HPC_PILOT_ROLE") == "admin"
        finally:
            if "HPC_PILOT_ROLE" in os.environ:
                del os.environ["HPC_PILOT_ROLE"]

    @patch("hpc_pilot.gateway.init_home")
    @patch("hpc_pilot.gateway.init_config")
    def test_gateway_no_role_emits_warning(self, mock_init_config, mock_init_home, capsys):
        """Starting without HPC_PILOT_ROLE prints a viewer-default warning."""
        if "HPC_PILOT_ROLE" in os.environ:
            del os.environ["HPC_PILOT_ROLE"]
        with patch("hpc_pilot.gateway.asyncio.run") as mock_run:
            mock_run.return_value = 0
            main(["--start"])
        captured = capsys.readouterr()
        assert "viewer" in captured.err.lower() or "HPC_PILOT_ROLE" in captured.err


class TestAllowlist:
    """Tests for _load_allowed_ids and gateway allowlist enforcement."""

    def test_load_allowed_ids_unset_returns_none(self):
        from hpc_pilot.gateway import _load_allowed_ids

        if "HPC_PILOT_TELEGRAM_ALLOWED_CHAT_IDS" in os.environ:
            del os.environ["HPC_PILOT_TELEGRAM_ALLOWED_CHAT_IDS"]
        assert _load_allowed_ids("HPC_PILOT_TELEGRAM_ALLOWED_CHAT_IDS") is None

    def test_load_allowed_ids_parses_ids(self):
        from hpc_pilot.gateway import _load_allowed_ids

        os.environ["HPC_PILOT_TELEGRAM_ALLOWED_CHAT_IDS"] = "111,222,333"
        try:
            result = _load_allowed_ids("HPC_PILOT_TELEGRAM_ALLOWED_CHAT_IDS")
        finally:
            del os.environ["HPC_PILOT_TELEGRAM_ALLOWED_CHAT_IDS"]
        assert result == {111, 222, 333}

    def test_telegram_gateway_blocks_unknown_chat(self):
        """TelegramGateway._is_allowed returns False for IDs not in the allowlist."""
        from hpc_pilot.gateway import TelegramGateway

        gw = TelegramGateway("tok", lambda: None, allowed_chat_ids={42, 99})
        assert gw._is_allowed(42) is True
        assert gw._is_allowed(999) is False

    def test_discord_gateway_blocks_unknown_user(self):
        """DiscordGateway._is_allowed returns False for IDs not in the allowlist."""
        from hpc_pilot.gateway import DiscordGateway

        gw = DiscordGateway("tok", lambda: None, allowed_user_ids={10, 20})
        assert gw._is_allowed(10) is True
        assert gw._is_allowed(30) is False

    def test_no_allowlist_allows_all(self):
        """allowed_chat_ids=None means no restriction."""
        from hpc_pilot.gateway import TelegramGateway

        gw = TelegramGateway("tok", lambda: None, allowed_chat_ids=None)
        assert gw._is_allowed(999999) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
