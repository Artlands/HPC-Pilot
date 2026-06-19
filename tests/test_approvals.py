"""Tests for Phase 8 approval workflow."""
from __future__ import annotations

import time

import pytest


class TestApprovals:
    def test_create_approval_returns_pending(self, tmp_home):
        from hpc_pilot.approvals import create_approval

        req = create_approval(
            tool="hpc_slurm_reconfigure",
            args={"dry_run": False},
            actor="alice",
            role="admin",
            cluster="default",
            risk_summary="Reconfigures Slurm controller",
        )
        assert req.status == "pending"
        assert req.id is not None
        assert req.tool == "hpc_slurm_reconfigure"
        assert req.requester_actor == "alice"
        assert req.expires_at > req.created_at

    def test_approve_request_changes_status(self, tmp_home):
        from hpc_pilot.approvals import approve_request, create_approval

        req = create_approval(
            tool="hpc_warewulf_image_delete",
            args={"name": "rocky9"},
            actor="bob",
            role="operator",
            cluster="default",
            risk_summary="Deletes a Warewulf image",
        )
        approved = approve_request(req.id, approver="admin")
        assert approved.status == "approved"
        assert approved.approver == "admin"
        assert approved.decided_at is not None

    def test_reject_request_changes_status(self, tmp_home):
        from hpc_pilot.approvals import create_approval, reject_request

        req = create_approval(
            tool="hpc_spack_env_delete",
            args={"name": "myenv"},
            actor="bob",
            role="operator",
            cluster="default",
            risk_summary="Deletes Spack environment",
        )
        rejected = reject_request(req.id, approver="admin")
        assert rejected.status == "rejected"
        assert rejected.approver == "admin"

    def test_get_approval_returns_none_for_unknown(self, tmp_home):
        from hpc_pilot.approvals import get_approval

        result = get_approval("nonexistent-id")
        assert result is None

    def test_list_pending_returns_only_pending(self, tmp_home):
        from hpc_pilot.approvals import approve_request, create_approval, list_pending

        r1 = create_approval("tool1", {}, "a", "op", "c", "risk1")
        r2 = create_approval("tool2", {}, "b", "op", "c", "risk2")
        approve_request(r1.id, "admin")

        pending = list_pending()
        ids = {r.id for r in pending}
        assert r2.id in ids  # still pending
        assert r1.id not in ids  # approved, not pending

    def test_approval_auto_expires_after_ttl(self, tmp_home):
        from hpc_pilot.approvals import create_approval, get_approval

        # Create an approval with 0 TTL so it expires immediately
        req = create_approval(
            tool="tool", args={}, actor="a", role="op",
            cluster="c", risk_summary="risk",
            ttl_hours=0,
        )
        # Make sure time has passed
        time.sleep(0.01)
        loaded = get_approval(req.id)
        assert loaded is not None
        assert loaded.status == "expired"

    def test_approval_decisions_are_audit_logged(self, tmp_home, audit_records):
        from hpc_pilot.approvals import approve_request, create_approval, reject_request

        req = create_approval("tool", {}, "alice", "op", "c", "risk")
        approve_request(req.id, "admin")
        # Rejecting an already-approved request should raise
        import pytest
        with pytest.raises(ValueError, match="not 'pending'"):
            reject_request(req.id, "admin2")

        records = audit_records()
        tools = {r["tool"] for r in records}
        assert "approval_created" in tools
        assert "approval_approved" in tools

    def test_re_approving_approved_returns_error(self, tmp_home):
        from hpc_pilot.approvals import approve_request, create_approval

        req = create_approval("tool", {}, "alice", "op", "c", "risk")
        approve_request(req.id, "admin")

        with pytest.raises(ValueError, match="not 'pending'"):
            approve_request(req.id, "admin2")

    def test_rejecting_already_rejected_returns_error(self, tmp_home):
        from hpc_pilot.approvals import create_approval, reject_request

        req = create_approval("tool", {}, "alice", "op", "c", "risk")
        reject_request(req.id, "admin")

        with pytest.raises(ValueError, match="not 'pending'"):
            reject_request(req.id, "admin2")
