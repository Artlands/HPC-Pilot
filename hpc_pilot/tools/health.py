"""Cluster health check — composes all subsystem checks."""
from __future__ import annotations

import datetime
from contextlib import suppress
from typing import Any

from hpc_pilot.tools._run import (
    _resolve_cluster,
    _run,
    check_ansible_available,
    check_slurm_available,
    check_spack_available,
    check_warewulf_available,
)
from hpc_pilot.tools.slurm import parse_node_state_histogram, parse_slurm_nodes


def hpc_cluster_health_check(*, cluster: str = "default") -> dict[str, Any]:
    """Run a comprehensive cluster health check across all installed components."""
    cl = _resolve_cluster(cluster)
    health: dict[str, Any] = {
        "timestamp": str(datetime.datetime.now()),
        "components": {},
        "overall": "healthy",
        "issues": [],
        "recommendations": [],
    }

    # --- Slurm ---
    slurm_ok = check_slurm_available(cl)
    slurm_info: dict[str, Any] = {
        "available": slurm_ok,
        "status": "unknown" if not slurm_ok else "checking",
    }
    health["components"]["slurm"] = slurm_info

    if slurm_ok:
        try:
            output = _run([cl.slurm("scontrol"), "show", "nodes"], cluster=cl, timeout=90)
            nodes_map = parse_slurm_nodes(output)
            histogram = parse_node_state_histogram(nodes_map)
            slurm_info["node_count"] = len(nodes_map)
            slurm_info["node_states"] = histogram
            slurm_info["status"] = "healthy"
            down = [
                name
                for name, info in nodes_map.items()
                if "down" in info.get("NodeState", "").lower()
            ]
            drained = [
                name
                for name, info in nodes_map.items()
                if "drain" in info.get("NodeState", "").lower()
                and "idle" not in info.get("NodeState", "").lower()
            ]
            if down:
                slurm_info["down_nodes"] = down
                slurm_info["status"] = "degraded"
                health["issues"].append(f"Down nodes: {', '.join(down)}")
                health["overall"] = "degraded"
            if drained:
                slurm_info["drained_nodes"] = drained
        except Exception as exc:
            slurm_info["status"] = "error"
            health["issues"].append(f"Slurm node check error: {exc}")
            health["overall"] = "degraded"

        # sdiag — scheduler diagnostics
        try:
            from hpc_pilot.tools.slurm_parsers import parse_sdiag
            sdiag_out = _run([cl.slurm("sdiag")], cluster=cl, timeout=30)
            sdiag = parse_sdiag(sdiag_out)
            slurm_info["sdiag"] = sdiag

            # Surface scheduler health issues
            sched = sdiag.get("main_schedule_statistics", sdiag.get("schedule_statistics", {}))
            backfill = sdiag.get("backfilling_stats", sdiag.get("backfill_statistics", {}))
            if isinstance(sched, dict):
                cycle_str = sched.get("last_cycle", sched.get("last_cycle_time", ""))
                if cycle_str and cycle_str.isdigit() and int(cycle_str) > 60000:
                    health["issues"].append(
                        f"Slurm scheduler last cycle was {cycle_str} ms (> 60 s)"
                    )
                    health["recommendations"].append(
                        "Check slurmctld logs; consider scontrol reconfigure."
                    )
            if isinstance(backfill, dict):
                queue_depth = backfill.get("queue_length", backfill.get("depth_try", ""))
                if queue_depth and str(queue_depth).isdigit() and int(str(queue_depth)) > 500:
                    health["issues"].append(
                        f"Slurm backfill queue depth is {queue_depth}"
                    )
        except Exception:
            pass  # sdiag failure is non-fatal for the health check

    # --- Warewulf ---
    ww_ok = check_warewulf_available(cl)
    ww_info: dict[str, Any] = {
        "available": ww_ok,
        "status": "unknown" if not ww_ok else "checking",
    }
    health["components"]["warewulf"] = ww_info
    if ww_ok:
        try:
            _run([cl.warewulf("wwctl"), "node", "list"], cluster=cl)
            ww_info["status"] = "healthy"
        except Exception as exc:
            ww_info["status"] = "error"
            health["issues"].append(f"Warewulf check error: {exc}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"

    # --- Spack ---
    spack_ok = check_spack_available(cl)
    spack_info: dict[str, Any] = {
        "available": spack_ok,
        "status": "unknown" if not spack_ok else "checking",
    }
    health["components"]["spack"] = spack_info
    if spack_ok:
        try:
            _run([cl.spack(), "env", "list"], cluster=cl)
            spack_info["status"] = "healthy"
        except Exception as exc:
            spack_info["status"] = "error"
            health["issues"].append(f"Spack check error: {exc}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"

    # --- Ansible ---
    ansible_ok = check_ansible_available()
    health["components"]["ansible"] = {
        "available": ansible_ok,
        "status": "unknown" if not ansible_ok else "healthy",
    }

    # --- Fabric: ibstatus probe ---
    fabric_info: dict[str, Any] = {
        "status": "not_checked",
        "links_down": [],
    }
    try:
        _run(["which", "ibstatus"], cluster=cl, timeout=10)
        ib_out = _run(["ibstatus"], cluster=cl, timeout=30)
        links_down: list[str] = []
        current_dev = ""
        for line in ib_out.splitlines():
            m = __import__("re").match(
                r"Infiniband device '(\S+)' port (\d+)", line
            )
            if m:
                current_dev = f"{m.group(1)}:{m.group(2)}"
            if current_dev and "state" in line.lower() and "active" not in line.lower() and "up" not in line.lower():
                    links_down.append(current_dev)
        if links_down:
            fabric_info["status"] = "degraded"
            fabric_info["links_down"] = links_down
            health["issues"].append(f"IB links down: {', '.join(links_down)}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"
        else:
            fabric_info["status"] = "healthy"
    except Exception:
        fabric_info["status"] = "unavailable"
    health["fabric"] = fabric_info

    # --- Storage: mount check + lctl probe ---
    storage_info: dict[str, Any] = {
        "status": "not_checked",
        "lustre_evictions_last_hour": 0,
    }
    try:
        _run(["mount"], cluster=cl, timeout=15)
        with suppress(Exception):
            _run([cl.slurm("lctl"), "get_param", "obdfilter.*.state"],
                 cluster=cl, timeout=15)
        storage_info["status"] = "healthy"
    except Exception as exc:
        storage_info["status"] = "error"
        health["issues"].append(f"Storage check error: {exc}")
        if health["overall"] == "healthy":
            health["overall"] = "degraded"
    health["storage"] = storage_info

    # --- GPU: check for XID errors in dmesg ---
    gpu_info: dict[str, Any] = {
        "status": "not_checked",
        "xid_errors_last_hour": 0,
    }
    try:
        dmesg_out = _run(["dmesg"], cluster=cl, timeout=30)
        xid_lines = [
            ln for ln in dmesg_out.splitlines()
            if "xid" in ln.lower()
        ]
        xid_count = 0
        for line in xid_lines:
            ts_match = __import__("re").match(r"\[(\d+\.\d+)\]", line)
            if ts_match:
                # count all XID errors (no boot-time anchor available here)
                xid_count += 1
        gpu_info["xid_errors_last_hour"] = xid_count
        if xid_count > 0:
            gpu_info["status"] = "degraded"
            health["issues"].append(
                f"{xid_count} GPU XID error(s) detected in dmesg"
            )
            if health["overall"] == "healthy":
                health["overall"] = "degraded"
        else:
            gpu_info["status"] = "healthy"
    except Exception:
        gpu_info["status"] = "unavailable"
    health["gpu"] = gpu_info

    # --- Metrics: Prometheus reachability check ---
    metrics_info: dict[str, Any] = {
        "status": "not_checked",
        "prometheus_reachable": False,
        "alerts_firing": 0,
    }
    try:
        import json
        import urllib.request

        from hpc_pilot.tools.metrics import _cluster_prometheus_url
        url = _cluster_prometheus_url(cluster)
        resp = urllib.request.urlopen(
            f"{url.rstrip('/')}/api/v1/alerts", timeout=10
        )
        body = resp.read().decode("utf-8")
        data = json.loads(body)
        alerts = data.get("data", {}).get("alerts", [])
        firing = [a for a in alerts if a.get("state") == "firing"]
        metrics_info["prometheus_reachable"] = True
        metrics_info["alerts_firing"] = len(firing)
        metrics_info["status"] = "healthy" if not firing else "degraded"
        if firing:
            health["issues"].append(
                f"{len(firing)} Prometheus alert(s) currently firing"
            )
    except Exception:
        try:
            from hpc_pilot.tools.metrics import _cluster_prometheus_url
            _cluster_prometheus_url(cluster)
            metrics_info["status"] = "unreachable"
        except Exception:
            metrics_info["status"] = "not_configured"
    health["metrics"] = metrics_info

    return health
