"""Out-of-band approval workflow for high-risk HPC operations."""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from hpc_pilot.audit import AuditEvent, log_audit
from hpc_pilot.paths import approvals_dir


@dataclass
class ApprovalRequest:
    """A pending (or decided) out-of-band approval request."""
    id: str
    tool: str
    args: dict[str, Any]
    requester_actor: str
    requester_role: str
    cluster: str
    risk_summary: str
    created_at: float
    expires_at: float
    status: Literal["pending", "approved", "rejected", "expired"]
    approver: str | None = None
    decided_at: float | None = None


def _request_path(request_id: str) -> str:
    return os.path.join(approvals_dir(), f"{request_id}.json")


def create_approval(
    tool: str,
    args: dict[str, Any],
    actor: str,
    role: str,
    cluster: str,
    risk_summary: str,
    *,
    ttl_hours: int = 24,
) -> ApprovalRequest:
    """Create a new out-of-band approval request and persist it to disk.

    Returns the newly created ApprovalRequest (status="pending").
    The request expires after *ttl_hours* (default 24).
    """
    now = time.time()
    request = ApprovalRequest(
        id=uuid.uuid4().hex,
        tool=tool,
        args=args,
        requester_actor=actor,
        requester_role=role,
        cluster=cluster,
        risk_summary=risk_summary,
        created_at=now,
        expires_at=now + (ttl_hours * 3600),
        status="pending",
    )
    _write(request)
    log_audit(AuditEvent(
        tool="approval_created",
        actor=actor,
        role=role,
        args={"approval_id": request.id, "target_tool": tool, "cluster": cluster,
              "risk_summary": risk_summary, "ttl_hours": ttl_hours},
        dry_run=False,
        returncode=0,
    ))
    return request


def get_approval(request_id: str) -> ApprovalRequest | None:
    """Load and return an ApprovalRequest by ID.

    Returns None if the request file does not exist.
    Automatically marks expired requests by updating the file in place.
    """
    path = _request_path(request_id)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    req = _from_dict(data)
    _auto_expire(req)
    return req


def approve_request(request_id: str, approver: str) -> ApprovalRequest:
    """Approve a pending approval request.

    Raises ValueError if the request is not found or not pending.
    """
    req = get_approval(request_id)
    if req is None:
        raise ValueError(f"Approval request not found: {request_id}")
    if req.status != "pending":
        raise ValueError(f"Approval request {request_id} is '{req.status}', not 'pending'")
    _auto_expire(req)
    if req.status != "pending":
        raise ValueError(f"Approval request {request_id} has expired")
    req.status = "approved"
    req.approver = approver
    req.decided_at = time.time()
    _write(req)
    log_audit(AuditEvent(
        tool="approval_approved",
        actor=approver,
        role="approver",
        args={"approval_id": request_id, "target_tool": req.tool,
              "requester": req.requester_actor, "cluster": req.cluster},
        dry_run=False,
        returncode=0,
    ))
    return req


def reject_request(request_id: str, approver: str) -> ApprovalRequest:
    """Reject a pending approval request.

    Raises ValueError if the request is not found or not pending.
    """
    req = get_approval(request_id)
    if req is None:
        raise ValueError(f"Approval request not found: {request_id}")
    if req.status != "pending":
        raise ValueError(f"Approval request {request_id} is '{req.status}', not 'pending'")
    _auto_expire(req)
    if req.status != "pending":
        raise ValueError(f"Approval request {request_id} has expired")
    req.status = "rejected"
    req.approver = approver
    req.decided_at = time.time()
    _write(req)
    log_audit(AuditEvent(
        tool="approval_rejected",
        actor=approver,
        role="approver",
        args={"approval_id": request_id, "target_tool": req.tool,
              "requester": req.requester_actor, "cluster": req.cluster},
        dry_run=False,
        returncode=0,
    ))
    return req


def list_pending() -> list[ApprovalRequest]:
    """Return all pending (non-expired) ApprovalRequests."""
    pending: list[ApprovalRequest] = []
    adir = approvals_dir()
    if not os.path.isdir(adir):
        return pending
    for name in os.listdir(adir):
        if not name.endswith(".json"):
            continue
        path = os.path.join(adir, name)
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        req = _from_dict(data)
        _auto_expire(req)
        if req.status == "pending":
            pending.append(req)
    return pending


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _write(req: ApprovalRequest) -> None:
    """Persist an ApprovalRequest to disk as JSON."""
    path = _request_path(req.id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(_to_dict(req), f, indent=2, default=str)


def _to_dict(req: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": req.id,
        "tool": req.tool,
        "args": req.args,
        "requester_actor": req.requester_actor,
        "requester_role": req.requester_role,
        "cluster": req.cluster,
        "risk_summary": req.risk_summary,
        "created_at": req.created_at,
        "expires_at": req.expires_at,
        "status": req.status,
        "approver": req.approver,
        "decided_at": req.decided_at,
    }


def _from_dict(data: dict[str, Any]) -> ApprovalRequest:
    return ApprovalRequest(
        id=data["id"],
        tool=data["tool"],
        args=data.get("args", {}),
        requester_actor=data["requester_actor"],
        requester_role=data["requester_role"],
        cluster=data.get("cluster", ""),
        risk_summary=data.get("risk_summary", ""),
        created_at=data["created_at"],
        expires_at=data["expires_at"],
        status=data["status"],
        approver=data.get("approver"),
        decided_at=data.get("decided_at"),
    )


def _auto_expire(req: ApprovalRequest) -> None:
    """If the request is pending and past its expiry, mark it expired in place."""
    if req.status == "pending" and time.time() >= req.expires_at:
        req.status = "expired"
        _write(req)
