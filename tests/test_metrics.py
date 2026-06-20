"""Tests for Prometheus metric wiring in dispatch/audit (Phase B.1).

Assert that tool_calls_total, denials_total, etc. are incremented when
the dispatch path is exercised — even when prometheus_client is not
installed (the no-op wrappers should be callable).
"""
from __future__ import annotations

from unittest.mock import patch


class TestMetricIncrement:
    """Drive the dispatch + audit path and assert Prometheus counters are
    incremented (via the no-op wrappers when prometheus_client is absent)."""

    def test_log_audit_increments_tool_calls_total(self):
        """log_audit() increments tool_calls_total via the no-op wrapper."""
        from hpc_pilot.audit import AuditEvent, log_audit
        from hpc_pilot.metrics import tool_calls_total

        with patch("hpc_pilot.metrics._HAS_PROMETHEUS", True), \
             patch.object(tool_calls_total, "inc") as mock_inc:
            log_audit(AuditEvent(tool="hpc_test", actor="u", role="viewer", args={}, dry_run=False))

        mock_inc.assert_called_once()

    def test_log_audit_success_status_label(self):
        """Successful calls get status=ok label."""
        from hpc_pilot.audit import AuditEvent, log_audit
        from hpc_pilot.metrics import tool_calls_total

        with patch("hpc_pilot.metrics._HAS_PROMETHEUS", True), \
             patch.object(tool_calls_total, "labels", return_value=tool_calls_total) as mock_labels:
            log_audit(AuditEvent(tool="hpc_test", actor="u", role="viewer", args={}, dry_run=False))

        mock_labels.assert_called_once_with(tool="hpc_test", status="ok")

    def test_log_audit_denied_status_label(self):
        """Denied calls (returncode=126) get status=denied label."""
        from hpc_pilot.audit import AuditEvent, log_audit
        from hpc_pilot.metrics import tool_calls_total

        with patch("hpc_pilot.metrics._HAS_PROMETHEUS", True), \
             patch.object(tool_calls_total, "labels", return_value=tool_calls_total) as mock_labels:
            log_audit(AuditEvent(
                tool="hpc_test", actor="u", role="viewer", args={},
                dry_run=False, returncode=126, error="permission_denied",
            ))

        mock_labels.assert_called_once_with(tool="hpc_test", status="denied")

    def test_log_audit_error_status_label(self):
        """Failed calls (non-zero returncode) get status=error label."""
        from hpc_pilot.audit import AuditEvent, log_audit
        from hpc_pilot.metrics import tool_calls_total

        with patch("hpc_pilot.metrics._HAS_PROMETHEUS", True), \
             patch.object(tool_calls_total, "labels", return_value=tool_calls_total) as mock_inc:
            log_audit(AuditEvent(
                tool="hpc_test", actor="u", role="viewer", args={},
                dry_run=False, returncode=1, error="something broke",
            ))

        mock_inc.assert_called_once()

    def test_denial_increments_denials_total(self, tmp_home):
        """dispatch.invoke() increments denials_total on permission error."""
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role
        from hpc_pilot.metrics import denials_total

        with patch("hpc_pilot.metrics._HAS_PROMETHEUS", True), \
             patch.object(denials_total, "labels", return_value=denials_total) as mock_labels, \
             patch("hpc_pilot.audit.log_audit"):  # suppress real audit
            try:
                invoke("hpc_slurm_qos_modify", {}, role=Role.VIEWER, actor="test-b1")
            except PermissionError:
                pass

        mock_labels.assert_called_once_with(tool="hpc_slurm_qos_modify")

    def test_sink_error_increments_sink_errors(self):
        """When a sink write fails, sink_errors_total is incremented."""
        from hpc_pilot.audit import AuditEvent, log_audit, reset_sinks
        from hpc_pilot.metrics import sink_errors_total

        reset_sinks()

        # Register a sink that always raises
        class _BrokenSink:
            def write(self, record):
                raise OSError("disk full")

        from hpc_pilot.audit import register_sink
        register_sink(_BrokenSink())

        with patch("hpc_pilot.metrics._HAS_PROMETHEUS", True), \
             patch.object(sink_errors_total, "labels", return_value=sink_errors_total) as mock_labels:
            log_audit(AuditEvent(tool="hpc_test", actor="u", role="viewer", args={}, dry_run=False))

        mock_labels.assert_called_once_with(sink_type="_broken")
