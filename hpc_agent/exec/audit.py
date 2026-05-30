"""Append-only audit log. See spec 00 §5.

Audit writes must succeed before a mutating action is reported OK. This module provides an
in-memory + pluggable sink; the production sink writes to the audit DB (WORM).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class CommandRecord(BaseModel):
    argv: list[str]  # redacted
    rc: int
    duration_s: float


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    actor: str
    agent_run_id: str | None = None
    tool: str
    risk: str
    input: dict[str, Any]  # redacted
    decision: str  # auto | approved-by:<who> | denied
    diff_summary: str | None = None
    commands: list[CommandRecord] = []
    result_status: str | None = None
    config_commit: str | None = None
    revert_argv: list[list[str]] | None = None  # inverse commands for state-revert


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...


class InMemorySink:
    """Default sink used in tests and local runs."""

    def __init__(self) -> None:
        self.events: dict[str, AuditEvent] = {}

    def write(self, event: AuditEvent) -> None:
        self.events[event.id] = event

    def get(self, audit_id: str) -> AuditEvent | None:
        return self.events.get(audit_id)


_sink: AuditSink = InMemorySink()


def set_sink(sink: AuditSink) -> None:
    global _sink
    _sink = sink


def new_event(*, actor: str, tool: str, risk: str, input: dict[str, Any]) -> AuditEvent:
    """Create (but do not yet persist) an event. Tools enrich it then call commit_event."""
    return AuditEvent(actor=actor, tool=tool, risk=risk, input=input, decision="pending")


def commit_event(event: AuditEvent) -> str:
    """Persist the event. Raises if the sink write fails (caller must abort the action)."""
    _sink.write(event)
    return event.id


def record_command(
    *, audit_id: str, actor: str, argv: list[str], rc: int, duration_s: float
) -> None:
    """Attach a command record to an existing event (best-effort; event may not exist yet
    in some sinks, so we tolerate absence)."""
    if isinstance(_sink, InMemorySink):
        event = _sink.get(audit_id)
        if event is not None:
            event.commands.append(CommandRecord(argv=argv, rc=rc, duration_s=duration_s))


def get_event(audit_id: str) -> AuditEvent | None:
    if isinstance(_sink, InMemorySink):
        return _sink.get(audit_id)
    return None
