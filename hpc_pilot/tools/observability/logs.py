"""Log inspection tools — slurmctld tail, slurmd journal, dmesg XID, journal search."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate
from hpc_pilot.tools.metrics.prometheus import _build_ssh_cmd

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_SECRET_RE = re.compile(r"(?i)(password|token|secret|api_key|apikey|passwd)\s*[=:]\s*\S+")


def _redact_log_line(line: str) -> str:
    """Strip sensitive values from a single log line."""
    line = _SECRET_RE.sub(r"\1=**REDACTED**", line)
    line = _EMAIL_RE.sub("**EMAIL-REDACTED**", line)
    return line


def _redact_output(output: str) -> str:
    """Redact *output* and optionally summarize if > 10 KB."""
    if len(output.encode("utf-8")) <= 10240:
        return _redact_log_line(output)

    lines = output.splitlines(keepends=True)
    redacted = [_redact_log_line(ln) for ln in lines]
    stripped = [ln.strip() for ln in redacted]
    top_n = Counter(stripped).most_common(5)
    summary_parts = [
        f"<output truncated: {len(lines)} lines, showing top-5 patterns>",
    ]
    for i, (pat, cnt) in enumerate(top_n):
        summary_parts.append(f"  [{i+1}] ({cnt}x) {pat[:120]}")
    return "\n".join(summary_parts)


# ---------------------------------------------------------------------------
# 8. Logs: slurmctld tail
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_logs_slurmctld_tail",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_logs_slurmctld_tail",
        "description": "Read the last N lines from /var/log/slurm/slurmctld.log, optionally filtered by a grep pattern.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to show (default: 50)",
                },
                "grep": {
                    "type": "string",
                    "description": "Optional grep -E pattern to filter lines",
                },
            },
            "required": [],
        },
    },
)
def hpc_logs_slurmctld_tail(
    lines: int = 50,
    grep: str | None = None,
    *,
    cluster: str = "default",
) -> str:
    """Read the last N lines from /var/log/slurm/slurmctld.log, optionally filtered."""
    if lines < 1:
        raise ValueError("lines must be >= 1")
    if grep is not None:
        _validate(grep, "grep pattern", re.compile(r"^[a-zA-Z0-9_ .|()*+?{}[\]^$-]+$"))
    cl = _resolve_cluster(cluster)

    if grep:
        raw = _run(
            ["tail", "-n", str(lines), "/var/log/slurm/slurmctld.log"],
            cluster=cl,
            timeout=30,
        )
        filtered = "\n".join(
            line for line in raw.splitlines() if grep in line
        )
        return _redact_output(filtered)
    else:
        cmd = ["tail", "-n", str(lines), "/var/log/slurm/slurmctld.log"]

    output = _run(cmd, cluster=cl, timeout=30)
    return _redact_output(output)


# ---------------------------------------------------------------------------
# 9. Logs: slurmd tail on a node
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_logs_slurmd_tail",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_logs_slurmd_tail",
        "description": "Read the last N lines of the slurmd journal on a compute node via SSH (journalctl -u slurmd).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Compute node name"},
                "lines": {
                    "type": "integer",
                    "description": "Number of lines to show (default: 50)",
                },
            },
            "required": ["node"],
        },
    },
)
def hpc_logs_slurmd_tail(
    node: str,
    lines: int = 50,
    *,
    cluster: str = "default",
) -> str:
    """Read the last N lines of slurmd journal on a compute node via SSH."""
    _validate(node, "node")
    if lines < 1:
        raise ValueError("lines must be >= 1")
    cl = _resolve_cluster(cluster)
    remote_cmd = ["journalctl", "-u", "slurmd", "-n", str(lines), "--no-pager"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)
    output = _run(cmd, timeout=60)
    return _redact_output(output)


# ---------------------------------------------------------------------------
# 10. Logs: dmesg XID errors
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_logs_dmesg_xid",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_logs_dmesg_xid",
        "description": "Search dmesg for GPU XID errors on a compute node via SSH. Returns parsed XID entries with timestamps.",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Compute node name"}},
            "required": ["node"],
        },
    },
)
def hpc_logs_dmesg_xid(
    node: str,
    *,
    cluster: str = "default",
) -> list[dict[str, str]]:
    """Search dmesg for GPU XID errors on a node via SSH."""
    _validate(node, "node")
    cl = _resolve_cluster(cluster)
    remote_cmd = ["sh", "-c", "dmesg | grep -i xid"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)

    try:
        output = _run(cmd, timeout=60)
    except RuntimeError:
        return []

    results: list[dict[str, str]] = []
    for line in output.splitlines():
        ts = ""
        ts_match = re.match(r"\[(\d+\.\d+)\]", line)
        if ts_match:
            ts = ts_match.group(1)
        results.append(
            {
                "timestamp": ts,
                "message": line.strip(),
            }
        )
    return results


# ---------------------------------------------------------------------------
# 11. Logs: search journald on controller
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_logs_search",
    role=Role.VIEWER,
    schema={
        "name": "hpc_logs_search",
        "description": "Search the Slurm controller's systemd journal (journalctl) for matching log lines.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "grep -E pattern to search for"},
                "since": {
                    "type": "string",
                    "description": "Time range, e.g. '24h ago', '7d ago' (default: '24h ago')",
                },
            },
            "required": ["pattern"],
        },
    },
)
def hpc_logs_search(
    pattern: str,
    since: str = "24h ago",
    *,
    cluster: str = "default",
) -> str:
    """Search the controller journal for matching log lines."""
    _validate(pattern, "search pattern")
    cl = _resolve_cluster(cluster)

    try:
        raw = _run(
            ["journalctl", f"--since={since}", "--no-pager"],
            cluster=cl,
            timeout=60,
        )
    except RuntimeError as exc:
        if "exited" in str(exc):
            return "(no matching lines)"
        raise

    filtered = "\n".join(
        line for line in raw.splitlines() if re.search(pattern, line)
    )
    output = _redact_output(filtered)
    return output if output.strip() else "(no matching lines)"
