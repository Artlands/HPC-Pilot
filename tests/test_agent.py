"""
Tests for hpc_pilot/agent.py.

The Anthropic client is mocked throughout so no real API calls are made.
Tests cover: tool dispatch, RBAC enforcement, conversation loop,
dry_run propagation, and error handling.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_agent(role_value: str = "admin") -> Any:
    """Create an HpcAgent with a mocked Anthropic client and the given role."""
    from hpc_pilot.agent import HpcAgent
    from hpc_pilot.rbac import Role

    role = Role(role_value)
    with patch("hpc_pilot.agent._load_env"):  # don't touch real .env
        agent = HpcAgent(model="claude-opus-4-7", role=role, actor="test-actor")
    agent._client = MagicMock()  # replace real Anthropic client
    return agent


def _text_response(text: str) -> MagicMock:
    """Return a mock messages.create response with stop_reason='end_turn'."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _tool_response(name: str, tool_id: str, args: dict[str, Any]) -> MagicMock:
    """Return a mock messages.create response with one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = tool_id
    block.input = args
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


# ---------------------------------------------------------------------------
# Tool dispatch (_call_tool)
# ---------------------------------------------------------------------------


class TestCallTool:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_node_status_dispatched(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="NodeName=n01", stderr="")
        agent = _make_agent()
        result = agent._call_tool("hpc_slurm_node_status", {"node": "n01"})
        assert "NodeName=n01" in result

    @patch("hpc_pilot.tools.subprocess.run")
    def test_queue_filters_mapped(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="JOBID ...", stderr="")
        agent = _make_agent()
        result = agent._call_tool("hpc_slurm_queue", {"user": "alice", "partition": "gpu"})
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
            result = agent._call_tool("hpc_cluster_health_check", {})
        parsed = json.loads(result)
        assert "overall" in parsed

    def test_qos_dry_run_default_true(self):
        agent = _make_agent()
        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            # dry_run=True → subprocess must NOT be called
            result = agent._call_tool("hpc_slurm_qos_modify", {"name": "gpu", "max_wall_min": 60})
        mock_run.assert_not_called()
        assert "DRY-RUN" in result

    def test_unknown_tool_returns_message(self):
        agent = _make_agent()
        result = agent._call_tool("hpc_does_not_exist", {})
        assert "unknown tool" in result

    @patch("hpc_pilot.tools.subprocess.run")
    def test_warewulf_bootstrap_dry_run(self, mock_run):
        agent = _make_agent()
        result = agent._call_tool("hpc_warewulf_bootstrap", {"node": "n01"})
        mock_run.assert_not_called()
        assert "DRY-RUN" in result

    @patch("hpc_pilot.tools.subprocess.run")
    def test_ansible_playbook_dry_run(self, mock_run):
        agent = _make_agent()
        result = agent._call_tool("hpc_ansible_playbook_run", {"playbook": "/p/play.yml"})
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
        # dry_run=False → subprocess called
        result = agent._execute_tool(
            "hpc_slurm_qos_modify", {"name": "gpu", "max_wall_min": 60, "dry_run": False}
        )
        assert result  # some output

    @patch("hpc_pilot.tools.subprocess.run")
    def test_viewer_allowed_node_status(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="NodeName=n01", stderr="")
        agent = _make_agent(role_value="viewer")
        result = agent._execute_tool("hpc_slurm_node_status", {"node": "n01"})
        assert "NodeName" in result

    def test_tool_error_surfaced_as_string(self):
        """RuntimeError from the tool is caught and returned as a string (not re-raised)."""
        agent = _make_agent(role_value="admin")
        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="permission denied")
            result = agent._execute_tool(
                "hpc_slurm_node_status", {"node": "n01", "dry_run": False}
            )
        assert "error" in result.lower() or "permission" in result.lower()


# ---------------------------------------------------------------------------
# run_turn — conversation loop
# ---------------------------------------------------------------------------


class TestRunTurn:
    def test_single_text_response(self):
        """Agent returns text immediately when stop_reason = end_turn."""
        agent = _make_agent()
        agent._client.messages.create.return_value = _text_response("The cluster is healthy.")

        text, history = agent.run_turn("Is the cluster OK?", [])

        assert text == "The cluster is healthy."
        assert len(history) == 2  # user message + assistant message
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"

    def test_tool_call_then_text(self):
        """Agent executes a tool and then produces a final text response."""
        agent = _make_agent()
        agent._client.messages.create.side_effect = [
            _tool_response("hpc_cluster_health_check", "toolu_01", {}),
            _text_response("Everything looks good."),
        ]

        with (
            patch("hpc_pilot.tools.check_slurm_available", return_value=False),
            patch("hpc_pilot.tools.check_warewulf_available", return_value=False),
            patch("hpc_pilot.tools.check_spack_available", return_value=False),
            patch("hpc_pilot.tools.check_ansible_available", return_value=False),
        ):
            text, history = agent.run_turn("How is the cluster?", [])

        assert text == "Everything looks good."
        # messages.create called twice: once with tool, once after tool result
        assert agent._client.messages.create.call_count == 2
        # history: user, assistant (tool call), user (tool result), assistant (text)
        assert len(history) == 4

    def test_streaming_path_calls_on_text(self):
        """When on_text is provided, the streaming API is used."""
        agent = _make_agent()

        chunks_received: list[str] = []
        mock_stream = MagicMock()
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.text_stream = iter(["Hello ", "world."])
        final = _text_response("Hello world.")
        mock_stream.get_final_message.return_value = final

        agent._client.messages.stream.return_value = mock_stream

        text, _ = agent.run_turn(
            "Hi",
            [],
            on_text=lambda c: chunks_received.append(c),
        )

        assert chunks_received == ["Hello ", "world."]
        agent._client.messages.stream.assert_called_once()
        agent._client.messages.create.assert_not_called()

    def test_on_tool_callback_called(self):
        """on_tool callback is invoked with tool name and args."""
        agent = _make_agent()
        agent._client.messages.create.side_effect = [
            _tool_response("hpc_spack_env_list", "toolu_02", {}),
            _text_response("Spack envs listed."),
        ]

        tool_calls: list[tuple[str, dict]] = []
        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="==> Environments", stderr="")
            text, _ = agent.run_turn(
                "List Spack environments",
                [],
                on_tool=lambda n, a: tool_calls.append((n, a)),
            )

        assert tool_calls[0][0] == "hpc_spack_env_list"

    def test_permission_error_returned_as_tool_result(self):
        """A PermissionError inside execute_tool is returned as a string to Claude."""
        from hpc_pilot.rbac import Role

        agent = _make_agent(role_value="viewer")
        agent._client.messages.create.side_effect = [
            _tool_response("hpc_slurm_qos_modify", "toolu_03", {"name": "gpu"}),
            _text_response("You do not have permission."),
        ]

        text, history = agent.run_turn("Modify the gpu QOS", [])

        # The tool result message (history[2]) should contain the permission error
        tool_result_msg = history[2]
        assert tool_result_msg["role"] == "user"
        content = tool_result_msg["content"]
        assert any("Permission" in str(c) for c in content)

    def test_history_preserved_across_turns(self):
        """Calling run_turn twice accumulates history correctly."""
        agent = _make_agent()
        agent._client.messages.create.side_effect = [
            _text_response("Turn 1 response."),
            _text_response("Turn 2 response."),
        ]

        _, history = agent.run_turn("First message", [])
        _, history = agent.run_turn("Second message", history)

        roles = [m["role"] for m in history]
        assert roles == ["user", "assistant", "user", "assistant"]

    def test_run_query_single_shot(self):
        """run_query wraps run_turn and returns just the text."""
        agent = _make_agent()
        agent._client.messages.create.return_value = _text_response("42 nodes available.")

        result = agent.run_query("How many nodes?")
        assert result == "42 nodes available."


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
