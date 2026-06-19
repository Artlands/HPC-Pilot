"""Warewulf tools and output parsers."""
from __future__ import annotations

from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate


def hpc_warewulf_node_status(*, cluster: str = "default") -> str:
    """Return wwctl node list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "node", "list"], cluster=cl)


def hpc_warewulf_image_list(*, cluster: str = "default") -> str:
    """Return wwctl image list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "image", "list"], cluster=cl)


def hpc_warewulf_power_reset(node: str, dry_run: bool = False, *, cluster: str = "default") -> str:
    """Power-reset a Warewulf node so it PXE-boots from its assigned image."""
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "reset", node],
        cluster=cl,
        timeout=120,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Warewulf output parsers
# ---------------------------------------------------------------------------


def parse_warewulf_nodes(output: str) -> list[dict[str, str]]:
    """Parse ``wwctl node list`` tabular output into a list of node dicts."""
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not header:
            header = stripped.split()
            continue
        parts = stripped.split(None, len(header) - 1)
        if parts:
            rows.append(dict(zip(header, parts, strict=False)))
    return rows
