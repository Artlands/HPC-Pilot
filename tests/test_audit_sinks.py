"""Tests for Phase 8 audit sinks."""
from __future__ import annotations

import json
import os
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

    def test_sink_registration_and_reset(self):
        from hpc_pilot.audit import FileSink, get_sinks, register_sink, reset_sinks

        reset_sinks()
        assert len(get_sinks()) == 0

        register_sink(FileSink("/tmp/test_sink.jsonl"))
        assert len(get_sinks()) == 1

        reset_sinks()
        assert len(get_sinks()) == 0
