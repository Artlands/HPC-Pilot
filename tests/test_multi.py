"""Tests for Phase 7 — Multi-cluster federation.

Covers:
- hpc_multi_query basic dispatch
- Partial success semantics (one cluster fails, others succeed)
- RBAC enforcement per cluster
- Dry run mode
- __init__.py re-export
- TOOL_SCHEMAS entry presence
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


class TestHpcMultiQuery:
    """Tests for hpc_multi_query — cross-cluster parallel query."""

    def test_basic_dispatch(self):
        """Two clusters both succeed: returns a dict with both results."""
        from hpc_pilot.tools.multi import hpc_multi_query

        with patch("hpc_pilot.tools.multi._query_single") as mq:
            mq.side_effect = lambda tool, args, cluster: f"result from {cluster}"
            result = hpc_multi_query(
                "hpc_slurm_queue", {"user": "alice"}, ["staging", "prod"]
            )

        assert result == {
            "staging": "result from staging",
            "prod": "result from prod",
        }
        assert mq.call_count == 2

    def test_partial_failure(self):
        """One cluster fails: the other's result is returned alongside the error."""
        from hpc_pilot.tools.multi import hpc_multi_query

        def _side_effect(tool, args, cluster):
            if cluster == "broken":
                raise RuntimeError("Connection refused")
            return f"{cluster} ok"

        with patch("hpc_pilot.tools.multi._query_single") as mq:
            mq.side_effect = _side_effect
            result = hpc_multi_query(
                "hpc_slurm_queue", {}, ["ok-cluster", "broken"]
            )

        assert result["ok-cluster"] == "ok-cluster ok"
        assert "[Error]" in result["broken"]
        assert "Connection refused" in result["broken"]

    def test_empty_clusters(self):
        """No clusters → empty dict."""
        from hpc_pilot.tools.multi import hpc_multi_query

        with patch("hpc_pilot.tools.multi._query_single") as mq:
            result = hpc_multi_query("hpc_slurm_queue", {}, [])
        assert result == {}
        mq.assert_not_called()

    def test_dry_run(self):
        """dry_run=True returns a descriptive message for each cluster without invoking queries."""
        from hpc_pilot.tools.multi import hpc_multi_query

        with patch("hpc_pilot.tools.multi._query_single") as mq:
            result = hpc_multi_query(
                "hpc_slurm_queue", {}, ["a", "b"], dry_run=True
            )

        mq.assert_not_called()
        assert sorted(result) == ["a", "b"]
        assert result["a"].startswith("DRY-RUN:")

    def test_rbac_enforcement(self):
        """Each cluster gets its own RBAC check; PermissionError caught per-cluster."""
        from hpc_pilot.tools.multi import hpc_multi_query

        class BrokenClusterError(RuntimeError):
            pass

        def _side_effect(tool, args, cluster):
            if cluster == "restricted":
                raise PermissionError("Tool 'hpc_multi_query' requires role 'superadmin'; current role is 'viewer'")
            return f"{cluster} ok"

        with patch("hpc_pilot.tools.multi._query_single") as mq:
            mq.side_effect = _side_effect

            result = hpc_multi_query(
                "hpc_multi_query", {}, ["normal", "restricted"]
            )

        assert result["normal"] == "normal ok"
        assert "Permission denied" in result["restricted"]

    def test_re_exported(self):
        """hpc_multi_query is re-exported from hpc_pilot.tools."""
        from hpc_pilot import tools
        assert hasattr(tools, "hpc_multi_query")
        assert callable(tools.hpc_multi_query)

    def test_schema_present(self):
        """TOOL_SCHEMAS contains an entry for hpc_multi_query."""
        from hpc_pilot.agent import TOOL_SCHEMAS
        names = [s["name"] for s in TOOL_SCHEMAS]
        assert "hpc_multi_query" in names

    def test_dispatch_entry_present(self):
        """_DISPATCH has an entry for hpc_multi_query."""
        from hpc_pilot.dispatch import _DISPATCH
        assert "hpc_multi_query" in _DISPATCH

    def test_rbac_entry_present(self):
        """TOOL_MIN_ROLE has an entry for hpc_multi_query at VIEWER level."""
        from hpc_pilot.rbac import TOOL_MIN_ROLE, Role
        assert "hpc_multi_query" in TOOL_MIN_ROLE
        assert TOOL_MIN_ROLE["hpc_multi_query"] == Role.VIEWER

    def test_rbac_viewer_can_call(self, tmp_home):
        """A VIEWER role can call hpc_multi_query via dispatch."""
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        from unittest.mock import MagicMock

        with patch("hpc_pilot.tools.multi._query_single") as mq:
            mq.return_value = "mocked"
            result = invoke(
                "hpc_multi_query",
                {"tool": "hpc_slurm_queue", "args": {}, "clusters": ["default"]},
                role=Role.VIEWER,
                actor="test",
            )
        assert "mocked" in result or '"default"' in result
