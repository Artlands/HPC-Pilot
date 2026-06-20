"""Audit log query tool."""

from __future__ import annotations

import io
import json
import os
from typing import Any

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool


@hpc_tool(
    name="hpc_audit_query",
    role=Role.VIEWER,
    schema={
        "name": "hpc_audit_query",
        "description": "Search and filter the HPC-Pilot audit log. Returns matching records as JSON lines, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "actor": {"type": "string"},
                "role": {"type": "string"},
                "error_only": {"type": "boolean"},
                "since_ts": {"type": "number"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
)
def hpc_audit_query(
    tool: str = "",
    actor: str = "",
    role: str = "",
    *,
    error_only: bool = False,
    since_ts: float | None = None,
    limit: int = 50,
    cluster: str = "default",
) -> str:
    """Search and filter the audit log programmatically.

    Reads from ``~/.hpc-pilot/logs/audit.jsonl`` and returns matching
    records as JSON lines, newest first.

    Args:
        tool: Filter by tool name (substring match).
        actor: Filter by actor name (substring match).
        role: Filter by role name (exact match).
        error_only: Only return records with a non-empty error field.
        since_ts: Unix timestamp — only return records after this time.
        limit: Maximum number of records to return (default 50).
    """
    audit_path = os.path.join(get_home(), "logs", "audit.jsonl")
    if not os.path.exists(audit_path):
        return "[]"

    matched: list[dict[str, Any]] = []
    with open(audit_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = record.get("ts", 0)
            if since_ts and ts < since_ts:
                continue
            if tool and tool not in record.get("tool", ""):
                continue
            if actor and actor not in record.get("actor", ""):
                continue
            if role and role != record.get("role", ""):
                continue
            if error_only and not record.get("error"):
                continue

            matched.append(record)
            if len(matched) >= limit:
                break

    # Newest first
    matched.sort(key=lambda r: r.get("ts", 0), reverse=True)

    if not matched:
        return "[]"

    out = io.StringIO()
    for rec in matched:
        out.write(json.dumps(rec, default=str) + "\n")
    return out.getvalue().rstrip()
