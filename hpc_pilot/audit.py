"""Audit logging for HPC Pilot tool invocations."""
from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Generator

from hpc_pilot.paths import audit_log_path

_SECRET_RE = re.compile(r"(token|key|password|secret|passwd)", re.IGNORECASE)


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    return {k: "***" if _SECRET_RE.search(k) else v for k, v in args.items()}


@dataclass
class AuditEvent:
    tool: str
    actor: str
    role: str
    args: dict[str, Any]
    dry_run: bool
    ts: float = field(default_factory=time.time)
    returncode: int = 0
    duration_ms: int = 0
    error: str = ""


def log_audit(event: AuditEvent) -> None:
    """Append one JSON line to the audit log; silently drops on I/O errors."""
    record: dict[str, Any] = {
        "ts": event.ts,
        "actor": event.actor,
        "role": event.role,
        "tool": event.tool,
        "args": _redact(event.args),
        "dry_run": event.dry_run,
        "returncode": event.returncode,
        "duration_ms": event.duration_ms,
    }
    if event.error:
        record["error"] = event.error
    try:
        with open(audit_log_path(), "a") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # audit failure must never block the tool


@contextmanager
def audit_tool(
    tool: str,
    actor: str,
    role: str,
    args: dict[str, Any],
    dry_run: bool,
) -> Generator[None, None, None]:
    """Context manager that records a tool invocation in the audit log."""
    event = AuditEvent(tool=tool, actor=actor, role=role, args=args, dry_run=dry_run)
    t0 = time.monotonic()
    try:
        yield
        event.duration_ms = int((time.monotonic() - t0) * 1000)
    except Exception as exc:
        event.duration_ms = int((time.monotonic() - t0) * 1000)
        event.returncode = 1
        event.error = str(exc)
        log_audit(event)
        raise
    log_audit(event)
