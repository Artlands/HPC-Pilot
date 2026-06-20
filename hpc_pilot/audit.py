"""Audit logging for HPC Pilot tool invocations."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import queue
import re
import threading
import time
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

from hpc_pilot.paths import audit_log_path

_SECRET_RE = re.compile(r"(token|key|password|secret|passwd)", re.IGNORECASE)

# Value-side secret patterns — scrub these tokens wherever they appear in values.
_VALUE_SECRET_RE = re.compile(
    r"("
    r"sk-[A-Za-z0-9_-]{20,}"                    # Anthropic / OpenAI API key
    r"|ghp_[A-Za-z0-9]{36}"                      # GitHub personal access token
    r"|xox[abprs]-[A-Za-z0-9-]{10,}"             # Slack token
    r"|Bearer\s+[A-Za-z0-9._-]+"                 # Bearer token
    r")",
)


def _redact(args: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for k, v in args.items():
        if _SECRET_RE.search(k):
            result[k] = "***"
        elif isinstance(v, str):
            result[k] = _VALUE_SECRET_RE.sub("***", v)
        else:
            result[k] = v
    return result


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
    """Append JSON lines to a file with optional size-bounded rotation.

    When the file exceeds *max_bytes*, it is renamed to ``<path>.1`` and
    existing rotated files (``.1``, ``.2``, …) are shifted.  At most
    *max_files* rotated backups are kept.
    """

    def __init__(
        self,
        path: str,
        max_bytes: int = 100 * 1024 * 1024,
        max_files: int = 5,
    ) -> None:
        self.path = os.path.expanduser(path)
        self.max_bytes = max_bytes
        self.max_files = max_files

    def _rotate(self) -> None:
        """Rename the current log file and shift backups."""
        # Remove the oldest backup if it exists
        oldest = f"{self.path}.{self.max_files}"
        if os.path.exists(oldest):
            try:
                os.remove(oldest)
            except OSError:
                pass
        # Shift existing backups: .N → .N+1
        for i in range(self.max_files - 1, 0, -1):
            src = f"{self.path}.{i}"
            dst = f"{self.path}.{i + 1}"
            if os.path.exists(src):
                try:
                    os.rename(src, dst)
                except OSError:
                    pass
        # Rename current log to .1
        try:
            os.rename(self.path, f"{self.path}.1")
        except OSError:
            pass

    def write(self, record: dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)

            # Check size before writing — rotate if needed
            try:
                if self.max_bytes > 0 and os.path.getsize(self.path) >= self.max_bytes:
                    self._rotate()
            except OSError:
                pass  # file doesn't exist yet, or stat failed

            with open(self.path, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(record) + "\n")
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass


class SyslogSink:
    """Write audit records to syslog via logging.handlers.SysLogHandler."""

    def __init__(self, facility: str = "local5") -> None:
        self.facility = facility
        self._handler: logging.Handler | None = None

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
            "lpr": logging.handlers.SysLogHandler.LOG_LPR,
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
            self._handler = None  # sentinel — skip logging

    def write(self, record: dict[str, Any]) -> None:
        try:
            self._ensure_handler()
            if self._handler is not None:
                logger = logging.getLogger("hpc_pilot_audit")
                msg = json.dumps(record, default=str)
                logger.info(msg)
        except Exception:
            pass


class HttpSink:
    """POST audit records as JSON to a URL via background queue + daemon thread.

    ``write()`` pushes to an in-process queue and returns immediately.
    A daemon consumer thread sends records to the remote endpoint.  On
    backpressure (queue exceeds *max_queue*), records are dropped and
    logged to stderr.
    """

    _MAX_QUEUE: int = 500
    _SEND_TIMEOUT: int = 5

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = dict(headers or {})
        if "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "application/json"
        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=self._MAX_QUEUE)
        self._consumer_started = False
        self._lock = threading.Lock()

    def _start_consumer(self) -> None:
        with self._lock:
            if self._consumer_started:
                return
            self._consumer_started = True

        def _consumer() -> None:
            while True:
                data = self._queue.get()
                if data is None:  # sentinel — shutdown
                    self._queue.task_done()
                    break
                try:
                    req = urllib.request.Request(self.url, data=data, headers=self.headers, method="POST")
                    urllib.request.urlopen(req, timeout=self._SEND_TIMEOUT)
                except Exception:
                    pass  # swallow per-sink errors
                finally:
                    self._queue.task_done()

        t = threading.Thread(target=_consumer, daemon=True, name="http-sink-consumer")
        t.start()

    def write(self, record: dict[str, Any]) -> None:
        try:
            self._start_consumer()
            data = json.dumps(record, default=str).encode("utf-8")
            self._queue.put(data, timeout=0.5)
        except queue.Full:
            import sys
            print("HttpSink: queue full, dropping audit record", file=sys.stderr)
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
                max_bytes = int(entry.get("rotation", {}).get("max_bytes", 100 * 1024 * 1024))
                max_files = int(entry.get("rotation", {}).get("max_files", 5))
                sinks.append(FileSink(sink_path, max_bytes=max_bytes, max_files=max_files))
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

    # Increment Prometheus counters (import-safe)
    try:
        from hpc_pilot.metrics import _HAS_PROMETHEUS, sink_errors_total, tool_calls_total

        if _HAS_PROMETHEUS:
            status = "ok"
            if event.returncode == 126:
                status = "denied"
            elif event.returncode != 0:
                status = "error"
            tool_calls_total.labels(tool=event.tool, status=status).inc()
    except Exception:
        pass

    # Always write to the primary file sink
    try:
        primary_sink = FileSink(audit_log_path())
        primary_sink.write(record)
    except Exception:
        pass  # primary sink must not interrupt

    # Write to all configured sinks
    for configured_sink in _get_sinks():
        try:
            configured_sink.write(record)
        except Exception:
            # Track sink errors in Prometheus
            try:
                from hpc_pilot.metrics import _HAS_PROMETHEUS as _HP, sink_errors_total as _set

                if _HP:
                    _set.labels(sink_type=type(configured_sink).__name__.replace("Sink", "").lower()).inc()
            except Exception:
                pass


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


def prune_audit_log(older_than_days: float = 90) -> int:
    """Remove audit log entries older than *older_than_days* days.

    Returns the number of records removed.  Rewrites the file in place.
    """
    import shutil

    path = audit_log_path()
    if not os.path.exists(path):
        return 0

    cutoff = time.time() - (older_than_days * 86400)
    tmp_path = path + ".tmp"
    kept = 0
    pruned = 0

    try:
        with open(path) as src, open(tmp_path, "w") as dst:
            for line in src:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = rec.get("ts", 0)
                    if ts < cutoff:
                        pruned += 1
                        continue
                except json.JSONDecodeError:
                    pruned += 1
                    continue
                dst.write(line + "\n")
                kept += 1

        shutil.move(tmp_path, path)
        return pruned
    except OSError:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        return 0


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
