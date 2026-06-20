"""Tests for Phase 8 audit sinks."""
from __future__ import annotations

import json
import os
import time
from unittest.mock import MagicMock, patch


class TestAuditSinks:
    def test_file_sink_writes_json_lines(self, tmp_home):
        from hpc_pilot.audit import FileSink

        log_path = os.path.join(tmp_home, "test_audit.jsonl")
        sink = FileSink(log_path)
        record = {"ts": 1000, "tool": "test", "actor": "alice", "role": "admin"}
        sink.write(record)

        with open(log_path) as f:
            line = f.readline().strip()
        loaded = json.loads(line)
        assert loaded["tool"] == "test"
        assert loaded["actor"] == "alice"

    @patch("hpc_pilot.audit.logging.getLogger")
    def test_syslog_sink_writes(self, mock_get_logger):
        from hpc_pilot.audit import SyslogSink

        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        sink = SyslogSink(facility="local5")
        record = {"ts": 2000, "tool": "test", "actor": "bob", "role": "operator"}
        sink.write(record)

        assert mock_get_logger.called  # called in __init__ and possibly in write
        mock_logger.info.assert_called_once()

    @patch("hpc_pilot.audit.urllib.request.urlopen")
    def test_http_sink_posts_to_url(self, mock_urlopen):
        from hpc_pilot.audit import HttpSink

        mock_resp = MagicMock()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        sink = HttpSink("http://example.com/audit")
        record = {"ts": 3000, "tool": "test", "actor": "carol", "role": "viewer"}
        sink.write(record)

        # The background consumer thread may not have processed the queue yet.
        deadline = time.monotonic() + 2.0
        while not mock_urlopen.called and time.monotonic() < deadline:
            time.sleep(0.05)

        mock_urlopen.assert_called_once()
        called_req = mock_urlopen.call_args[0][0]
        assert called_req.method == "POST"
        assert called_req.full_url == "http://example.com/audit"
        assert called_req.headers.get("Content-Type") == "application/json" \
            or called_req.headers.get("Content-type") == "application/json"

    def test_one_sink_failure_does_not_raise(self, tmp_home):
        """A failing sink should not prevent other sinks from receiving records."""
        from hpc_pilot.audit import AuditEvent, FileSink, log_audit, reset_sinks

        reset_sinks()

        # Register a failing sink
        class FailingSink:
            def write(self, record):
                raise RuntimeError("oh no!")

        from hpc_pilot.audit import register_sink
        register_sink(FailingSink())
        register_sink(FileSink(os.path.join(tmp_home, "fallback.jsonl")))

        # This should not raise
        log_audit(AuditEvent(
            tool="test_tool", actor="alice", role="admin",
            args={}, dry_run=False,
        ))

        # Verify fallback sink was written
        with open(os.path.join(tmp_home, "fallback.jsonl")) as f:
            line = f.readline().strip()
        assert "test_tool" in line

    def test_multiple_sinks_all_receive(self, tmp_home):
        from hpc_pilot.audit import AuditEvent, FileSink, log_audit, register_sink, reset_sinks

        reset_sinks()

        path1 = os.path.join(tmp_home, "sink1.jsonl")
        path2 = os.path.join(tmp_home, "sink2.jsonl")

        register_sink(FileSink(path1))
        register_sink(FileSink(path2))

        log_audit(AuditEvent(
            tool="multi_sink_test", actor="alice", role="admin",
            args={}, dry_run=False,
        ))

        for path in [path1, path2]:
            with open(path) as f:
                line = f.readline().strip()
            assert "multi_sink_test" in line

    # -------------------------------------------------------------------
    # Value-side secret redaction (B.2)
    # -------------------------------------------------------------------

    def test_redact_anthropic_key_in_value(self):
        """Anthropic sk-... key in argument value is redacted."""
        from hpc_pilot.audit import _redact

        redacted = _redact({"cmd": "echo sk-abc123def456ghi789jkl012mno345"})
        assert "sk-abc123def456ghi789jkl012mno345" not in redacted["cmd"]
        assert "***" in redacted["cmd"]

    def test_redact_github_token_in_value(self):
        """GitHub ghp_ token in argument value is redacted."""
        from hpc_pilot.audit import _redact

        redacted = _redact({"url": "https://ghp_abcdef123456789012345678901234567890@github.com"})
        assert "ghp_abcdef123456789012345678901234567890" not in redacted["url"]

    def test_redact_slack_token_in_value(self):
        """Slack xoxb- token in argument value is redacted."""
        from hpc_pilot.audit import _redact

        redacted = _redact({"token": "xoxb-123456789012-123456789012-abc123def456"})
        # Key-based redaction fires first for key "token"
        assert redacted["token"] == "***"

    def test_redact_slack_token_in_command_value(self):
        """Slack token embedded in a command string is redacted via value pattern."""
        from hpc_pilot.audit import _redact

        redacted = _redact({"cmd": "slack_cli --token=xoxp-123456789012-abc123def456ghi"})
        assert "xoxp-123456789012-abc123def456ghi" not in redacted["cmd"]
        assert "***" in redacted["cmd"]

    def test_redact_bearer_token_in_value(self):
        """Bearer token in argument value is redacted."""
        from hpc_pilot.audit import _redact

        redacted = _redact({"headers": "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.test"})
        assert "Bearer eyJhbGciOiJIUzI1NiJ9.test" not in redacted["headers"]

    def test_redact_preserves_innocent_values(self):
        """Non-secret values are left untouched."""
        from hpc_pilot.audit import _redact

        redacted = _redact({"node": "compute01", "cluster": "prod", "user": "alice"})
        assert redacted["node"] == "compute01"
        assert redacted["cluster"] == "prod"
        assert redacted["user"] == "alice"

    def test_redact_empty_args(self):
        """Empty args dict is unchanged."""
        from hpc_pilot.audit import _redact

        assert _redact({}) == {}

    def test_redact_non_string_value(self):
        """Non-string values are passed through."""
        from hpc_pilot.audit import _redact

        redacted = _redact({"count": 42, "enabled": True, "tags": ["a", "b"]})
        assert redacted["count"] == 42
        assert redacted["enabled"] is True
        assert redacted["tags"] == ["a", "b"]

    def test_sink_registration_and_reset(self):
        from hpc_pilot.audit import FileSink, get_sinks, register_sink, reset_sinks

        reset_sinks()
        assert len(get_sinks()) == 0

        register_sink(FileSink("/tmp/test_sink.jsonl"))
        assert len(get_sinks()) == 1

        reset_sinks()
        assert len(get_sinks()) == 0

    # -------------------------------------------------------------------
    # Audit log rotation (B.3)
    # -------------------------------------------------------------------

    def test_file_sink_rotates_at_capacity(self, tmp_home):
        """FileSink rotates the log file when it reaches max_bytes."""
        from hpc_pilot.audit import FileSink

        log_path = os.path.join(tmp_home, "rotate_audit.jsonl")
        sink = FileSink(log_path, max_bytes=100, max_files=3)

        # Write enough records to trigger at least one rotation
        for i in range(50):
            sink.write({"ts": i, "tool": f"tool_{i}"})

        # At least one rotated backup should exist
        backup_1 = f"{log_path}.1"
        assert os.path.exists(backup_1) or os.path.getsize(log_path) < 200

    def test_file_sink_rotation_keeps_max_files(self, tmp_home):
        """No more than max_files backups are retained after rotation."""
        from hpc_pilot.audit import FileSink

        log_path = os.path.join(tmp_home, "rotate_limit.jsonl")
        sink = FileSink(log_path, max_bytes=50, max_files=3)

        # Write many records to trigger multiple rotations
        for i in range(200):
            sink.write({"ts": i, "tool": "t", "data": "x" * 20})

        backups = [f for f in os.listdir(tmp_home) if f.startswith("rotate_limit.jsonl.")]
        assert len(backups) <= 3

    def test_file_sink_no_rotation_when_below_capacity(self, tmp_home):
        """No rotation occurs when the log stays below max_bytes."""
        from hpc_pilot.audit import FileSink

        log_path = os.path.join(tmp_home, "no_rotate.jsonl")
        sink = FileSink(log_path, max_bytes=100 * 1024 * 1024, max_files=3)

        for i in range(5):
            sink.write({"ts": i, "tool": "t"})

        # No backup files should exist
        backups = [f for f in os.listdir(tmp_home) if f.startswith("no_rotate.jsonl.")]
        assert len(backups) == 0
