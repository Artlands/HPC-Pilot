"""
Tests for hpc_pilot/agent.py.

The Hermes CLI subprocess is mocked throughout so no real agent calls are made.
Tests cover: tool dispatch, RBAC enforcement, conversation loop,
session persistence, and schema completeness.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(role_value: str = "admin") -> Any:
    """Create an HpcAgent with the given role (no mocked subprocess)."""
    from hpc_pilot.agent import HpcAgent
    from hpc_pilot.rbac import Role

    role = Role(role_value)
    with patch("hpc_pilot.agent._load_env"):
        agent = HpcAgent(model="test-model", role=role, actor="test-actor")
    return agent


# ---------------------------------------------------------------------------
# Tool dispatch (_execute_tool)
# ---------------------------------------------------------------------------


class TestExecuteTool:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_node_status_dispatched(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="NodeName=n01", stderr="")
        agent = _make_agent()
        result = agent._execute_tool("hpc_slurm_node_status", {"node": "n01"})
        assert "NodeName=n01" in result

    @patch("hpc_pilot.tools.subprocess.run")
    def test_queue_filters_mapped(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="JOBID ...", stderr="")
        agent = _make_agent()
        agent._execute_tool("hpc_slurm_queue", {"user": "alice", "partition": "gpu"})
        argv = mock_run.call_args[0][0]
        assert "--user" in argv and "alice" in argv
        assert "--partition" in argv and "gpu" in argv

    @patch("hpc_pilot.tools.subprocess.run")
    def test_health_check_returns_json(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        agent = _make_agent()
        with (
            patch("hpc_pilot.tools.check_slurm_available", return_value=False),
            patch("hpc_pilot.tools.check_warewulf_available", return_value=False),
            patch("hpc_pilot.tools.check_spack_available", return_value=False),
            patch("hpc_pilot.tools.check_ansible_available", return_value=False),
        ):
            result = agent._execute_tool("hpc_cluster_health_check", {})
        parsed = json.loads(result)
        assert "overall" in parsed

    def test_qos_dry_run_default_true(self):
        agent = _make_agent()
        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            result = agent._execute_tool(
                "hpc_slurm_qos_modify", {"name": "gpu", "max_wall_min": 60}
            )
        mock_run.assert_not_called()
        assert "DRY-RUN" in result

    def test_unknown_tool_raises_permission_error(self):
        agent = _make_agent()
        with pytest.raises(PermissionError, match="requires role.*superadmin"):
            agent._execute_tool("hpc_does_not_exist", {})

    @patch("hpc_pilot.tools.subprocess.run")
    def test_warewulf_power_reset_dry_run(self, mock_run):
        agent = _make_agent()
        result = agent._execute_tool("hpc_warewulf_power_reset", {"node": "n01"})
        mock_run.assert_not_called()
        assert "DRY-RUN" in result

    @patch("hpc_pilot.tools.subprocess.run")
    def test_ansible_playbook_dry_run(self, mock_run):
        agent = _make_agent()
        result = agent._execute_tool(
            "hpc_ansible_playbook_run", {"playbook": "/p/play.yml"}
        )
        mock_run.assert_not_called()
        assert "DRY-RUN" in result


# ---------------------------------------------------------------------------
# RBAC enforcement (_execute_tool)
# ---------------------------------------------------------------------------


class TestExecuteToolRbac:
    def test_viewer_denied_qos_modify(self):
        agent = _make_agent(role_value="viewer")
        with pytest.raises(PermissionError, match="requires role 'admin'"):
            agent._execute_tool("hpc_slurm_qos_modify", {"name": "gpu", "dry_run": False})

    def test_viewer_denied_ansible(self):
        agent = _make_agent(role_value="viewer")
        with pytest.raises(PermissionError):
            agent._execute_tool("hpc_ansible_playbook_run", {"playbook": "/p.yml", "dry_run": False})

    def test_operator_denied_qos(self):
        agent = _make_agent(role_value="operator")
        with pytest.raises(PermissionError):
            agent._execute_tool("hpc_slurm_qos_modify", {"name": "gpu"})

    @patch("hpc_pilot.tools.subprocess.run")
    def test_admin_allowed_qos_dry_run(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="Modified", stderr="")
        agent = _make_agent(role_value="admin")
        result = agent._execute_tool(
            "hpc_slurm_qos_modify", {"name": "gpu", "max_wall_min": 60, "dry_run": False}
        )
        assert result

    @patch("hpc_pilot.tools.subprocess.run")
    def test_viewer_allowed_node_status(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="NodeName=n01", stderr="")
        agent = _make_agent(role_value="viewer")
        result = agent._execute_tool("hpc_slurm_node_status", {"node": "n01"})
        assert "NodeName" in result

    def test_tool_error_surfaced_as_string(self):
        agent = _make_agent(role_value="admin")
        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
            result = agent._execute_tool(
                "hpc_slurm_node_status", {"node": "n01", "dry_run": False}
            )
        assert "error" in result.lower() or "permission" in result.lower()


# ---------------------------------------------------------------------------
# run_turn — delegates to ``hermes chat -q`` subprocess
# ---------------------------------------------------------------------------


class TestRunTurn:
    @patch("hpc_pilot.agent._find_hermes", return_value="/usr/bin/hermes")
    @patch("hpc_pilot.agent.subprocess.run")
    def test_single_text_response(self, mock_run, mock_find):
        """Agent returns text from the Hermes subprocess."""
        mock_run.return_value = MagicMock(returncode=0, stdout="The cluster is healthy.")
        agent = _make_agent()
        text, history = agent.run_turn("Is the cluster OK?", [])

        assert text == "The cluster is healthy."
        # Verify subprocess was called with correct args
        args = mock_run.call_args[0][0]
        assert "-q" in args
        assert "Is the cluster OK?" in args
        assert "-t" in args
        assert "hpc" in args

    @patch("hpc_pilot.agent._find_hermes", return_value="/usr/bin/hermes")
    @patch("hpc_pilot.agent.subprocess.run")
    def test_streaming_path_calls_on_text(self, mock_run, mock_find):
        """When on_text is provided, Hermes output is streamed."""
        mock_run.return_value = MagicMock(returncode=0, stdout="Hello world.")
        agent = _make_agent()
        chunks_received: list[str] = []
        text, _ = agent.run_turn("Hi", [], on_text=lambda c: chunks_received.append(c))

        assert "Hello world." in text
        assert len(chunks_received) >= 1

    @patch("hpc_pilot.agent._find_hermes", return_value="/usr/bin/hermes")
    @patch("hpc_pilot.agent.subprocess.run")
    def test_run_query_single_shot(self, mock_run, mock_find):
        """run_query wraps run_turn and returns just the text."""
        mock_run.return_value = MagicMock(returncode=0, stdout="42 nodes available.")
        agent = _make_agent()
        result = agent.run_query("How many nodes?")

        assert result == "42 nodes available."


# ---------------------------------------------------------------------------
# run_chat_loop — execs ``hermes chat -t hpc``
# ---------------------------------------------------------------------------


class TestRunChatLoop:
    @patch("hpc_pilot.agent._find_hermes", return_value="/usr/bin/hermes")
    @patch("hpc_pilot.agent.os.execvp")
    def test_execs_hermes_chat_with_hpc_toolset(self, mock_exec, mock_find):
        """run_chat_loop calls execvp with 'hermes chat -t hpc'."""
        from hpc_pilot.agent import run_chat_loop

        agent = _make_agent()
        rc = run_chat_loop(agent)

        assert rc == 0
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "/usr/bin/hermes"
        assert "chat" in args[1]
        assert "-t" in args[1]
        assert "hpc" in args[1]

    def test_hermes_not_found(self):
        """run_chat_loop returns 1 when hermes is not found."""
        from hpc_pilot.agent import run_chat_loop

        agent = _make_agent()
        with patch("hpc_pilot.agent._find_hermes", return_value="/nonexistent/hermes"):
            with patch("hpc_pilot.agent.os.execvp", side_effect=FileNotFoundError):
                rc = run_chat_loop(agent)

        assert rc == 1


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


class TestSessionPersistence:
    def test_save_and_load_roundtrip(self, tmp_path):
        """save_session -> load_session returns identical messages."""
        from hpc_pilot.agent import load_session, save_session

        agent = _make_agent()
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
        ]
        with patch("hpc_pilot.paths.get_home", return_value=str(tmp_path)):
            sid = save_session(history, agent)
            loaded, meta = load_session(sid)

        assert loaded == history
        assert meta["model"] == agent.model
        assert meta["role"] == agent.role.value

    def test_new_session_id_unique(self, tmp_path):
        """_new_session_id returns a different name if the first one exists."""
        from hpc_pilot.agent import _new_session_id

        with patch("hpc_pilot.paths.sessions_dir", return_value=str(tmp_path)):
            first = _new_session_id()
            (tmp_path / f"{first}.json").write_text("{}")
            second = _new_session_id()

        assert first != second

    def test_list_sessions_sorted_newest_first(self, tmp_path):
        """list_sessions returns sessions in descending timestamp order."""
        import time as _time

        from hpc_pilot.agent import list_sessions, save_session

        agent = _make_agent()
        history = [{"role": "user", "content": "q"}]
        with patch("hpc_pilot.paths.get_home", return_value=str(tmp_path)):
            save_session(history, agent, session_id="older")
            _time.sleep(0.01)
            save_session(history, agent, session_id="newer")
            sessions = list_sessions()

        assert sessions[0]["id"] == "newer"
        assert sessions[1]["id"] == "older"

    def test_list_sessions_empty_when_no_dir(self, tmp_path):
        """list_sessions returns [] when the sessions directory doesn't exist."""
        from hpc_pilot.agent import list_sessions

        with patch("hpc_pilot.paths.sessions_dir", return_value=str(tmp_path / "nonexistent")):
            assert list_sessions() == []

    def test_load_session_missing_raises(self, tmp_path):
        """load_session raises FileNotFoundError for unknown session IDs."""
        from hpc_pilot.agent import load_session

        with patch("hpc_pilot.paths.sessions_dir", return_value=str(tmp_path)), pytest.raises(FileNotFoundError):
            load_session("does-not-exist")

    def test_serialize_sdk_blocks(self):
        """_serialize_message converts SDK-like objects with model_dump to plain dicts."""
        from hpc_pilot.agent import _serialize_message

        block = MagicMock()
        block.model_dump.return_value = {"type": "text", "text": "hello"}
        msg = {"role": "assistant", "content": [block]}
        result = _serialize_message(msg)
        assert result["content"] == [{"type": "text", "text": "hello"}]


# ---------------------------------------------------------------------------
# TOOL_SCHEMAS completeness
# ---------------------------------------------------------------------------


class TestToolSchemas:
    def test_all_schemas_have_required_fields(self):
        from hpc_pilot.agent import TOOL_SCHEMAS

        for schema in TOOL_SCHEMAS:
            assert "name" in schema, f"missing 'name' in {schema}"
            assert "description" in schema, f"missing 'description' in {schema!r}"
            assert "input_schema" in schema, f"missing 'input_schema' in {schema['name']}"
            assert "type" in schema["input_schema"]
            assert "properties" in schema["input_schema"]

    def test_schema_names_match_tool_functions(self):
        """Every schema name must correspond to a callable in hpc_pilot.tools."""
        from hpc_pilot import tools
        from hpc_pilot.agent import TOOL_SCHEMAS

        for schema in TOOL_SCHEMAS:
            name = schema["name"]
            assert hasattr(tools, name), f"No function hpc_pilot.tools.{name}"
            assert callable(getattr(tools, name))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
