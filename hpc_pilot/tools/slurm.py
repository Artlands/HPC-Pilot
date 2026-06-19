"""Slurm tools and output parsers."""
from __future__ import annotations

import re
from typing import Any

from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _USER_RE, _validate

# ---------------------------------------------------------------------------
# Slurm tools
# ---------------------------------------------------------------------------


def hpc_slurm_node_status(node: str = "", *, cluster: str = "default") -> str:
    """Return scontrol node info for *node*, or all nodes when *node* is empty."""
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("scontrol"), "show", "node"]
    if node:
        cmd.append(node)
    return _run(cmd, cluster=cl, timeout=90)


def hpc_slurm_queue(
    filters: dict[str, str] | None = None,
    *,
    cluster: str = "default",
) -> str:
    """Return squeue output, optionally filtered.

    Supported filter keys: ``user``, ``partition``, ``state``.
    """
    allowed_filters = {"user", "partition", "state"}
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("squeue")]
    if filters:
        for key, value in filters.items():
            if key not in allowed_filters:
                raise ValueError(f"Unknown filter key: {key!r}")
            _validate(value, key, _USER_RE)
            cmd.extend([f"--{key.replace('_', '-')}", value])
    return _run(cmd, cluster=cl)


def hpc_slurm_node_state(
    node: str,
    target: str,
    reason: str | None = None,
    dry_run: bool = False,
    *,
    cluster: str = "default",
) -> str:
    """Change a Slurm node's state (drain / resume / down / undrain)."""
    _validate(node, "node name")
    allowed = {"drain", "resume", "down", "undrain"}
    if target not in allowed:
        raise ValueError(f"Invalid target state: {target!r}. Must be one of {sorted(allowed)}")
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("scontrol"), "update", f"node={node}", f"state={target}"]
    if reason:
        cmd.append(f"reason={reason}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


def hpc_slurm_qos_modify(
    name: str,
    max_wall_min: int | None = None,
    dry_run: bool = False,
    *,
    cluster: str = "default",
) -> str:
    """Modify a Slurm QOS entry."""
    _validate(name, "QOS name", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("sacctmgr"), "--immediate", "modify", "qos", name, "set"]
    if max_wall_min is not None:
        cmd.append(f"MaxWall={max_wall_min}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Slurm output parsers
# ---------------------------------------------------------------------------


def parse_slurm_queue(output: str) -> list[dict[str, str]]:
    """Parse ``squeue`` tabular output into a list of job dicts."""
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("---"):
            continue
        if not header and stripped.upper().startswith("JOBID"):
            header = stripped.split()
            continue
        if header:
            parts = stripped.split(None, len(header) - 1)
            if parts:
                rows.append(dict(zip(header, parts, strict=False)))
    return rows


def parse_slurm_nodes(output: str) -> dict[str, Any]:
    """Parse ``scontrol show node`` output into a mapping keyed by node name."""
    nodes: dict[str, Any] = {}
    current: dict[str, Any] = {}

    for line in output.splitlines():
        for key, value in re.findall(r"(\w+)=(\S+)", line):
            if key == "NodeName":
                if current and "NodeName" in current:
                    nodes[current["NodeName"]] = current
                current = {"NodeName": value}
            elif current:
                current[key] = value

    if current and "NodeName" in current:
        nodes[current["NodeName"]] = current

    return nodes
