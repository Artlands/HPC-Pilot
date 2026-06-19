"""Cluster health check — composes all subsystem checks."""
from __future__ import annotations

import datetime
from typing import Any

from hpc_pilot.tools._run import (
    _resolve_cluster,
    _run,
    check_ansible_available,
    check_slurm_available,
    check_spack_available,
    check_warewulf_available,
)
from hpc_pilot.tools.slurm import parse_slurm_nodes


def hpc_cluster_health_check(*, cluster: str = "default") -> dict[str, Any]:
    """Run a comprehensive cluster health check across all installed components."""
    cl = _resolve_cluster(cluster)
    health: dict[str, Any] = {
        "timestamp": str(datetime.datetime.now()),
        "components": {},
        "overall": "healthy",
        "issues": [],
    }

    # --- Slurm ---
    slurm_ok = check_slurm_available(cl)
    health["components"]["slurm"] = {
        "available": slurm_ok,
        "status": "unknown" if not slurm_ok else "checking",
    }
    if slurm_ok:
        try:
            output = _run([cl.slurm("scontrol"), "show", "nodes"], cluster=cl, timeout=90)
            nodes_status = parse_slurm_nodes(output)
            health["components"]["slurm"]["nodes"] = len(nodes_status)
            health["components"]["slurm"]["status"] = "healthy"
            down = [
                name
                for name, info in nodes_status.items()
                if "down" in info.get("NodeState", "").lower()
            ]
            if down:
                health["components"]["slurm"]["status"] = "degraded"
                health["issues"].append(f"Down nodes: {', '.join(down)}")
                health["overall"] = "degraded"
        except Exception as exc:
            health["components"]["slurm"]["status"] = "error"
            health["issues"].append(f"Slurm check error: {exc}")
            health["overall"] = "degraded"

    # --- Warewulf ---
    ww_ok = check_warewulf_available(cl)
    health["components"]["warewulf"] = {
        "available": ww_ok,
        "status": "unknown" if not ww_ok else "checking",
    }
    if ww_ok:
        try:
            _run([cl.warewulf("wwctl"), "node", "list"], cluster=cl)
            health["components"]["warewulf"]["status"] = "healthy"
        except Exception as exc:
            health["components"]["warewulf"]["status"] = "error"
            health["issues"].append(f"Warewulf check error: {exc}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"

    # --- Spack ---
    spack_ok = check_spack_available(cl)
    health["components"]["spack"] = {
        "available": spack_ok,
        "status": "unknown" if not spack_ok else "checking",
    }
    if spack_ok:
        try:
            _run([cl.spack(), "env", "list"], cluster=cl)
            health["components"]["spack"]["status"] = "healthy"
        except Exception as exc:
            health["components"]["spack"]["status"] = "error"
            health["issues"].append(f"Spack check error: {exc}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"

    # --- Ansible ---
    ansible_ok = check_ansible_available()
    health["components"]["ansible"] = {
        "available": ansible_ok,
        "status": "unknown" if not ansible_ok else "healthy",
    }

    return health
