"""Append-only audit log. See spec 00 §5.

Audit writes must succeed before a mutating action is reported OK. This module provides an
in-memory + pluggable sink; the production sink writes to the audit DB (WORM).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine

from hpc_agent.config.settings import settings


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
    commands: list[CommandRecord] = Field(default_factory=list)
    result_status: str | None = None
    config_commit: str | None = None
    revert_argv: list[list[str]] | None = None  # inverse commands for state-revert


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None: ...
    def record_command(self, audit_id: str, command: CommandRecord) -> None: ...
    def get(self, audit_id: str) -> AuditEvent | None: ...


class InMemorySink:
    """Default sink used in tests and local runs."""

    def __init__(self) -> None:
        self.events: dict[str, AuditEvent] = {}
        self.pending_commands: dict[str, list[CommandRecord]] = {}

    def write(self, event: AuditEvent) -> None:
        pending = self.pending_commands.pop(event.id, [])
        if pending:
            event.commands.extend(pending)
        self.events[event.id] = event

    def get(self, audit_id: str) -> AuditEvent | None:
        return self.events.get(audit_id)

    def record_command(self, audit_id: str, command: CommandRecord) -> None:
        event = self.events.get(audit_id)
        if event is not None:
            event.commands.append(command)
            return
        self.pending_commands.setdefault(audit_id, []).append(command)


_sink: AuditSink = InMemorySink()


_metadata = MetaData()

audit_events = Table(
    "audit_events",
    _metadata,
    Column("id", String(64), primary_key=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("actor", String(255), nullable=False),
    Column("agent_run_id", String(64), nullable=True),
    Column("tool", String(255), nullable=False),
    Column("risk", String(32), nullable=False),
    Column("input", JSON, nullable=False),
    Column("decision", String(255), nullable=False),
    Column("diff_summary", Text, nullable=True),
    Column("result_status", String(64), nullable=True),
    Column("config_commit", String(128), nullable=True),
    Column("revert_argv", JSON, nullable=True),
    Index("ix_audit_events_ts", "ts"),
    Index("ix_audit_events_actor", "actor"),
    Index("ix_audit_events_tool", "tool"),
    Index("ix_audit_events_result_status", "result_status"),
)

audit_commands = Table(
    "audit_commands",
    _metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("audit_id", String(64), ForeignKey("audit_events.id"), nullable=False),
    Column("seq", Integer, nullable=False),
    Column("argv", JSON, nullable=False),
    Column("rc", Integer, nullable=False),
    Column("duration_s", Float, nullable=False),
    Index("ix_audit_commands_audit_id", "audit_id"),
)


class DatabaseAuditSink:
    """SQL-backed audit sink for durable operation tracking."""

    def __init__(self, url: str | None = None, *, echo: bool = False, init_schema: bool = False):
        self.engine = create_engine(url or settings.audit_db_url, echo=echo, future=True)
        self.pending_commands: dict[str, list[CommandRecord]] = {}
        if init_schema:
            init_audit_db(self.engine)

    def write(self, event: AuditEvent) -> None:
        pending = self.pending_commands.pop(event.id, [])
        commands = [*event.commands, *pending]
        with self.engine.begin() as conn:
            conn.execute(
                audit_events.insert().values(
                    id=event.id,
                    ts=event.ts,
                    actor=event.actor,
                    agent_run_id=event.agent_run_id,
                    tool=event.tool,
                    risk=event.risk,
                    input=event.input,
                    decision=event.decision,
                    diff_summary=event.diff_summary,
                    result_status=event.result_status,
                    config_commit=event.config_commit,
                    revert_argv=event.revert_argv,
                )
            )
            if commands:
                conn.execute(
                    audit_commands.insert(),
                    [
                        {
                            "audit_id": event.id,
                            "seq": idx,
                            "argv": command.argv,
                            "rc": command.rc,
                            "duration_s": command.duration_s,
                        }
                        for idx, command in enumerate(commands)
                    ],
                )

    def record_command(self, audit_id: str, command: CommandRecord) -> None:
        with self.engine.begin() as conn:
            exists = conn.scalar(
                select(audit_events.c.id).where(audit_events.c.id == audit_id).limit(1)
            )
            if exists is None:
                self.pending_commands.setdefault(audit_id, []).append(command)
                return
            seq = conn.scalar(
                select(audit_commands.c.seq)
                .where(audit_commands.c.audit_id == audit_id)
                .order_by(audit_commands.c.seq.desc())
                .limit(1)
            )
            conn.execute(
                audit_commands.insert().values(
                    audit_id=audit_id,
                    seq=0 if seq is None else int(seq) + 1,
                    argv=command.argv,
                    rc=command.rc,
                    duration_s=command.duration_s,
                )
            )

    def get(self, audit_id: str) -> AuditEvent | None:
        with self.engine.begin() as conn:
            row = (
                conn.execute(select(audit_events).where(audit_events.c.id == audit_id))
                .mappings()
                .one_or_none()
            )
            if row is None:
                return None
            command_rows = conn.execute(
                select(audit_commands)
                .where(audit_commands.c.audit_id == audit_id)
                .order_by(audit_commands.c.seq)
            ).mappings()
            return AuditEvent(
                id=str(row["id"]),
                ts=row["ts"],
                actor=str(row["actor"]),
                agent_run_id=row["agent_run_id"],
                tool=str(row["tool"]),
                risk=str(row["risk"]),
                input=dict(row["input"]),
                decision=str(row["decision"]),
                diff_summary=row["diff_summary"],
                commands=[
                    CommandRecord(
                        argv=list(command["argv"]),
                        rc=int(command["rc"]),
                        duration_s=float(command["duration_s"]),
                    )
                    for command in command_rows
                ],
                result_status=row["result_status"],
                config_commit=row["config_commit"],
                revert_argv=row["revert_argv"],
            )

    def list_events(
        self,
        *,
        limit: int = 20,
        tool: str | None = None,
        actor: str | None = None,
        result_status: str | None = None,
    ) -> list[AuditEvent]:
        stmt = select(audit_events).order_by(audit_events.c.ts.desc()).limit(limit)
        if tool is not None:
            stmt = stmt.where(audit_events.c.tool == tool)
        if actor is not None:
            stmt = stmt.where(audit_events.c.actor == actor)
        if result_status is not None:
            stmt = stmt.where(audit_events.c.result_status == result_status)
        with self.engine.begin() as conn:
            rows = conn.execute(stmt).mappings()
            return [self.get(str(row["id"])) for row in rows if row["id"] is not None]  # type: ignore[misc]


def set_sink(sink: AuditSink) -> None:
    global _sink
    _sink = sink


def init_audit_db(engine_or_url: Engine | str | None = None) -> None:
    """Create audit tables. Production can run this through migrations/bootstrap."""
    engine = (
        engine_or_url
        if isinstance(engine_or_url, Engine)
        else create_engine(engine_or_url or settings.audit_db_url, future=True)
    )
    _metadata.create_all(engine)


def use_database_sink(
    url: str | None = None, *, echo: bool = False, init_schema: bool = False
) -> DatabaseAuditSink:
    """Install and return a durable SQL audit sink."""
    sink = DatabaseAuditSink(url, echo=echo, init_schema=init_schema)
    set_sink(sink)
    return sink


def configure_from_settings() -> None:
    """Configure the process-wide audit sink from environment-backed settings."""
    if settings.audit_sink == "db":
        use_database_sink(settings.audit_db_url, init_schema=settings.audit_auto_init)
    else:
        set_sink(InMemorySink())


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
    """Attach a command record to an event, buffering it if the event is not committed yet."""
    _sink.record_command(audit_id, CommandRecord(argv=argv, rc=rc, duration_s=duration_s))


def get_event(audit_id: str) -> AuditEvent | None:
    return _sink.get(audit_id)


def list_events(
    *,
    limit: int = 20,
    tool: str | None = None,
    actor: str | None = None,
    result_status: str | None = None,
) -> list[AuditEvent]:
    if isinstance(_sink, DatabaseAuditSink):
        return _sink.list_events(
            limit=limit,
            tool=tool,
            actor=actor,
            result_status=result_status,
        )
    if isinstance(_sink, InMemorySink):
        events = list(_sink.events.values())
        if tool is not None:
            events = [event for event in events if event.tool == tool]
        if actor is not None:
            events = [event for event in events if event.actor == actor]
        if result_status is not None:
            events = [event for event in events if event.result_status == result_status]
        return sorted(events, key=lambda event: event.ts, reverse=True)[:limit]
    return []
