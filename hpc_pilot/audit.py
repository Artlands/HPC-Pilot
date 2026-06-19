"""Audit logging for HPC Pilot tool invocations."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

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
    usage: dict[str, int] | None = None


# ---------------------------------------------------------------------------
# Pluggable audit sinks
# ---------------------------------------------------------------------------


class AuditSink(Protocol):
    """Protocol for pluggable audit sinks."""

    def write(self, record: dict[str, Any]) -> None:
        """Write one audit record. Must not raise exceptions."""
        ...


class FileSink:
    """Append JSON lines to a file."""

    def __init__(self, path: str) -> None:
        self.path = os.path.expanduser(path)

    def write(self, record: dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            pass


class SyslogSink:
    """Write audit records to syslog via logging.handlers.SysLogHandler."""

    def __init__(self, facility: str = "local5") -> None:
        self.facility = facility
        self._handler = None

    def _ensure_handler(self) -> None:
        if self._handler is not None:
            return
        import logging
        import logging.handlers
        from socket import SOCK_DGRAM

        # Map facility name to SysLogHandler constant
        facility_map = {
            "kern": logging.handlers.SysLogHandler.LOG_KERN,
            "user": logging.handlers.SysLogHandler.LOG_USER,
            "mail": logging.handlers.SysLogHandler.LOG_MAIL,
            "daemon": logging.handlers.SysLogHandler.LOG_DAEMON,
            "auth": logging.handlers.SysLogHandler.LOG_AUTH,
            "syslog": logging.handlers.SysLogHandler.LOG_SYSLOG,
            "lpr": logging.handlers.SysLogHandler.LPR,
            "news": logging.handlers.SysLogHandler.LOG_NEWS,
            "uucp": logging.handlers.SysLogHandler.LOG_UUCP,
            "cron": logging.handlers.SysLogHandler.LOG_CRON,
            "local0": logging.handlers.SysLogHandler.LOG_LOCAL0,
            "local1": logging.handlers.SysLogHandler.LOG_LOCAL1,
            "local2": logging.handlers.SysLogHandler.LOG_LOCAL2,
            "local3": logging.handlers.SysLogHandler.LOG_LOCAL3,
            "local4": logging.handlers.SysLogHandler.LOG_LOCAL4,
            "local5": logging.handlers.SysLogHandler.LOG_LOCAL5,
            "local6": logging.handlers.SysLogHandler.LOG_LOCAL6,
            "local7": logging.handlers.SysLogHandler.LOG_LOCAL7,
        }
        facility_const = facility_map.get(self.facility, logging.handlers.SysLogHandler.LOG_LOCAL5)
        logger = logging.getLogger("hpc_pilot_audit")
        logger.setLevel(logging.INFO)
        try:
            handler = logging.handlers.SysLogHandler(
                facility=facility_const,
                socktype=SOCK_DGRAM,
            )
            formatter = logging.Formatter(
                "hpc-pilot[%(process)d]: %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            self._handler = handler
        except Exception:
            # If SysLogHandler cannot be created (e.g. on macOS without syslogd),
            # fall back to a no-op
            self._handler = object()  # sentinel

    def write(self, record: dict[str, Any]) -> None:
        try:
            self._ensure_handler()
            if isinstance(self._handler, logging.Handler):
                import logging
                logger = logging.getLogger("hpc_pilot_audit")
                msg = json.dumps(record, default=str)
                logger.info(msg)
        except Exception:
            pass


class HttpSink:
    """POST audit records as JSON to a URL via urllib."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = dict(headers or {})
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json"

    def write(self, record: dict[str, Any]) -> None:
        try:
            data = json.dumps(record, default=str).encode("utf-8")
            req = urllib.request.Request(self.url, data=data, headers=self.headers, method="POST")
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Sink registry
# ---------------------------------------------------------------------------

_SINKS: list[AuditSink] = []
_SINKS_LOADED = False


def _load_sinks_from_config() -> list[AuditSink]:
    """Read sinks from config.yaml. Returns empty list on any error."""
    try:
        import yaml
        from hpc_pilot.paths import config_path
        path = config_path()
        if not os.path.exists(path):
            return []
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        raw_sinks = data.get("audit", {}).get("sinks", []) or []
        sinks: list[AuditSink] = []
        for entry in raw_sinks:
            sink_type = entry.get("type", "file")
            if sink_type == "file":
                sink_path = entry.get("path", audit_log_path())
                sinks.append(FileSink(sink_path))
            elif sink_type == "syslog":
                facility = entry.get("facility", "local5")
                sinks.append(SyslogSink(facility=facility))
            elif sink_type == "http":
                url = entry.get("url", "")
                if url:
                    headers = entry.get("headers")
                    sinks.append(HttpSink(url, headers=headers))
        return sinks
    except Exception:
        return []


def _get_sinks() -> list[AuditSink]:
    """Return the current list of audit sinks. Reloads from config once."""
    global _SINKS, _SINKS_LOADED
    if not _SINKS_LOADED:
        configured = _load_sinks_from_config()
        _SINKS.extend(configured)
        _SINKS_LOADED = True
    return _SINKS


# Allow tests to reset / inject sinks
def reset_sinks() -> None:
    """Clear all sinks (used in tests)."""
    global _SINKS, _SINKS_LOADED
    _SINKS.clear()
    _SINKS_LOADED = False


def register_sink(sink: AuditSink) -> None:
    """Register an additional audit sink at runtime."""
    _get_sinks()
    _SINKS.append(sink)


def get_sinks() -> list[AuditSink]:
    """Return the current list of registered audit sinks."""
    return list(_get_sinks())


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


def log_audit(event: AuditEvent) -> None:
    """Append one JSON line to all configured audit sinks.

    The primary file sink (~/.hpc-pilot/logs/audit.jsonl) is always active.
    Additional sinks are loaded from config.yaml (audit.sinks).
    All sinks silently swallow errors — one failing sink must not block others.
    """
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
    if event.usage:
        record["usage"] = event.usage

    # Always write to the primary file sink
    sink = FileSink(audit_log_path())
    sink.write(record)

    # Write to all configured sinks
    for configured_sink in _get_sinks():
        configured_sink.write(record)


def log_llm_usage(
    actor: str,
    role: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Append one LLM token-usage record to the audit log."""
    event = AuditEvent(
        tool="llm_call",
        actor=actor,
        role=role,
        args={"model": model},
        dry_run=False,
        usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
    )
    log_audit(event)


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
