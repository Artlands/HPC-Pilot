"""Prometheus query tools and cluster metrics aggregation."""

from __future__ import annotations

import json
import os
import re
import shlex
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, cast

from hpc_pilot.paths import config_path
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run

_MOUNT_LINE_RE = re.compile(r"^(\S+) on (\S+) type (\S+)")
_DF_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+.*)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cluster_prometheus_config(cluster_name: str) -> dict[str, Any]:
    """Read the full Prometheus config for a cluster from config.yaml."""
    try:
        import yaml
    except ImportError:
        raise RuntimeError("PyYAML is required to read config for Prometheus URL") from None
    path = config_path()
    if not os.path.exists(path):
        raise RuntimeError("config.yaml not found — cannot determine Prometheus URL")
    with open(path) as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}
    obs = data.get("observability", {}) or {}
    clusters_raw = data.get("clusters", {}) or {}
    cluster_cfg = clusters_raw.get(cluster_name, {}) or {}
    cluster_obs = cluster_cfg.get("observability", {}) or {}
    # Merge: cluster-specific overrides global defaults
    merged: dict[str, Any] = dict(obs)  # global defaults
    merged.update(cluster_obs)  # cluster-specific overrides
    prom = merged.get("prometheus", {})
    return {"url": prom.get("url", ""), "auth": prom.get("auth", {})}


def _cluster_prometheus_url(cluster_name: str) -> str:
    """Read the Prometheus URL from the cluster's config.yaml."""
    cfg = _cluster_prometheus_config(cluster_name)
    url = cfg.get("url", "")
    if not url:
        raise RuntimeError(
            f"Prometheus URL not configured for cluster {cluster_name!r} — "
            "set observability.prometheus.url in config.yaml"
        )
    # Resolve auth tokens via SecretsManager if configured
    auth = cfg.get("auth", {})
    if auth.get("type") == "bearer":
        token_key = auth.get("token_secret_key", "PROMETHEUS_TOKEN")
        from hpc_pilot.secrets import get_secrets_manager

        token = get_secrets_manager().get(token_key)
        if token:
            parsed = urllib.parse.urlparse(url)
            netloc = f"{token}:@{parsed.hostname}"
            if parsed.port:
                netloc += f":{parsed.port}"
            url = urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    return str(url)


def _build_ssh_cmd(node: str, cluster: Any, remote_cmd: list[str]) -> list[str]:
    """Build a local command list that SSHes to *node* and runs *remote_cmd*.

    Uses the cluster's SSH config for the controller.  Falls back to the
    node name directly when the cluster does not carry SSH settings.
    """
    if cluster is not None and cluster.ssh is not None:
        ssh = cluster.ssh

        cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-i",
            os.path.expanduser(ssh.key),
            f"{ssh.user}@{node}",
            "--",
            *map(shlex.quote, remote_cmd),
        ]
        if ssh.control_path:
            cmd[1:1] = [
                "-o",
                f"ControlPath={ssh.control_path}",
                "-o",
                "ControlMaster=auto",
            ]
        return cmd
    return ["ssh", node, *map(shlex.quote, remote_cmd)]


# ---------------------------------------------------------------------------
# 1. Prometheus query
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_metrics_prometheus_query",
    role=Role.VIEWER,
    schema={
        "name": "hpc_metrics_prometheus_query",
        "description": "Query the Prometheus HTTP API (instant or range query). Returns JSON results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "PromQL query string"},
                "start": {
                    "type": "string",
                    "description": "Range start time (RFC3339 or Unix timestamp)",
                },
                "end": {"type": "string", "description": "Range end time"},
                "step": {
                    "type": "string",
                    "description": "Query resolution step width (e.g. '15s')",
                },
            },
            "required": ["query"],
        },
    },
)
def hpc_metrics_prometheus_query(
    query: str,
    start: str | None = None,
    end: str | None = None,
    step: str | None = None,
    *,
    cluster: str = "default",
) -> dict[str, Any]:
    """Query the Prometheus HTTP API and return JSON results."""
    base_url = _cluster_prometheus_url(cluster)
    params: dict[str, str] = {"query": query}
    if start:
        params["start"] = start
    if end:
        params["end"] = end
    if step:
        params["step"] = step

    api_path = "/api/v1/query_range" if start or end or step else "/api/v1/query"

    url = f"{base_url.rstrip('/')}{api_path}?{urllib.parse.urlencode(params)}"
    try:
        resp = urllib.request.urlopen(url, timeout=30)
        body = resp.read().decode("utf-8")
        return cast(dict[str, Any], json.loads(body))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Prometheus HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Prometheus unreachable: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Prometheus returned invalid JSON: {exc}") from exc


# ---------------------------------------------------------------------------
# 2. Prometheus alerts
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_metrics_prometheus_alerts",
    role=Role.VIEWER,
    schema={
        "name": "hpc_metrics_prometheus_alerts",
        "description": "Fetch active (firing/pending) alerts from Prometheus /api/v1/alerts.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_metrics_prometheus_alerts(*, cluster: str = "default") -> list[dict[str, Any]]:
    """Fetch active alerts from Prometheus /api/v1/alerts."""
    base_url = _cluster_prometheus_url(cluster)
    url = f"{base_url.rstrip('/')}/api/v1/alerts"
    try:
        resp = urllib.request.urlopen(url, timeout=30)
        body = resp.read().decode("utf-8")
        data: dict[str, Any] = cast(dict[str, Any], json.loads(body))
        alerts: list[dict[str, Any]] = cast(
            list[dict[str, Any]], data.get("data", {}).get("alerts", [])
        )
        return alerts
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Prometheus alerts HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Prometheus alerts unreachable: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# 3. Storage: Lustre status
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_storage_lustre_status",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_storage_lustre_status",
        "description": "Check Lustre filesystem health — OST/MDT state via lctl get_param. Returns per-target status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_storage_lustre_status(*, cluster: str = "default") -> dict[str, Any]:
    """Check Lustre OST/MDT state via lctl get_param on the controller."""
    cl = _resolve_cluster(cluster)
    result: dict[str, Any] = {
        "osts": {},
        "mdts": {},
        "status": "unknown",
        "error": None,
    }

    _LCTL = "/usr/sbin/lctl"
    try:
        ost_output = _run(
            [_LCTL, "get_param", "obdfilter.*.state"],
            cluster=cl,
            timeout=30,
        )
        for line in ost_output.strip().splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                result["osts"][key.strip()] = val.strip()
    except Exception as exc:
        result["error"] = str(exc)

    try:
        mdt_output = _run(
            [_LCTL, "get_param", "mdt.*.state"],
            cluster=cl,
            timeout=30,
        )
        for line in mdt_output.strip().splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                result["mdts"][key.strip()] = val.strip()
    except Exception:
        pass

    if result["error"]:
        result["status"] = "error"
    elif not result["osts"]:
        result["status"] = "not_available"
    else:
        all_ok = all(v.lower() in ("online", "active") for v in result["osts"].values())
        result["status"] = "healthy" if all_ok else "degraded"

    return result


# ---------------------------------------------------------------------------
# 4. Storage: mounts
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_storage_mounts",
    role=Role.VIEWER,
    schema={
        "name": "hpc_storage_mounts",
        "description": "Show mounted filesystems and disk usage (mount + df -h) on the cluster controller.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_storage_mounts(*, cluster: str = "default") -> dict[str, Any]:
    """Inspect current mounts and disk usage on the controller."""
    cl = _resolve_cluster(cluster)
    result: dict[str, Any] = {
        "mounts": [],
        "disk_usage": [],
    }

    try:
        mount_out = _run(["mount"], cluster=cl, timeout=15)
        for line in mount_out.strip().splitlines():
            m = _MOUNT_LINE_RE.match(line)
            if m:
                result["mounts"].append(
                    {
                        "device": m.group(1),
                        "mount_point": m.group(2),
                        "filesystem": m.group(3),
                    }
                )
    except Exception:
        pass

    try:
        df_out = _run(["df", "-h"], cluster=cl, timeout=15)
        lines = df_out.strip().splitlines()
        if lines:
            for line in lines[1:]:
                m = _DF_LINE_RE.match(line)
                if m:
                    result["disk_usage"].append(
                        {
                            "filesystem": m.group(1),
                            "size": m.group(2),
                            "used": m.group(3),
                            "available": m.group(4),
                            "use_percent": m.group(5),
                            "mounted_on": m.group(6).strip(),
                        }
                    )
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# 5. Fabric: IB link status
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_fabric_ib_link_status",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_fabric_ib_link_status",
        "description": "Check InfiniBand link status (ibstatus) on a node via SSH. Returns link rate, state, and per-port info.",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Compute node name"}},
            "required": ["node"],
        },
    },
)
def hpc_fabric_ib_link_status(
    node: str,
    *,
    cluster: str = "default",
) -> dict[str, Any]:
    """Run ibstatus on a node via SSH and parse link state/rate/errors."""
    cl = _resolve_cluster(cluster)
    remote_cmd = ["ibstatus"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)

    result: dict[str, Any] = {
        "node": node,
        "links": [],
        "status": "unknown",
    }

    try:
        output = _run(cmd, timeout=60)
    except RuntimeError as exc:
        result["error"] = str(exc)
        result["status"] = "unavailable"
        return result

    current_iface: dict[str, Any] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        m = re.match(r"Infiniband device '(\S+)' port (\d+)", line)
        if m:
            if current_iface:
                result["links"].append(current_iface)
            current_iface = {"device": m.group(1), "port": m.group(2)}
            continue

        if current_iface:
            lm = re.match(r"rate[\s:]+(.*)", line, re.IGNORECASE)
            if lm:
                current_iface["rate"] = lm.group(1).strip()
            sm = re.match(r"state[\s:]+(.*)", line, re.IGNORECASE)
            if sm:
                current_iface["state"] = sm.group(1).strip()
            lm2 = re.match(r"phys_state[\s:]+(.*)", line, re.IGNORECASE)
            if lm2:
                current_iface["phys_state"] = lm2.group(1).strip()

    if current_iface:
        result["links"].append(current_iface)

    states = {link.get("state", "").lower() for link in result["links"]}
    if not result["links"]:
        result["status"] = "no_interfaces"
    elif "active" in states or "up" in states:
        result["status"] = "healthy" if "down" not in states else "degraded"
    else:
        result["status"] = "down"

    return result


# ---------------------------------------------------------------------------
# 6. Node summary
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_metrics_node_summary",
    role=Role.VIEWER,
    schema={
        "name": "hpc_metrics_node_summary",
        "description": "Aggregate observability metrics for a single compute node — GPU state, InfiniBand link status, and GPU XID errors.",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Compute node name"}},
            "required": ["node"],
        },
    },
)
def hpc_metrics_node_summary(
    node: str,
    *,
    cluster: str = "default",
) -> dict[str, Any]:
    """Aggregate key observability metrics for a single compute node.

    Combines nvidia-smi, IB link status, and dmesg XID checks into one
    summary.  Returns whatever data is available; missing sections indicate
    the relevant subsystem was not reachable.
    """
    import contextlib

    from hpc_pilot.tools.observability.gpu import hpc_gpu_nvidia_smi
    from hpc_pilot.tools.observability.logs import hpc_logs_dmesg_xid

    summary: dict[str, Any] = {
        "node": node,
        "gpu": {},
        "fabric": {},
        "xid_errors": [],
    }

    # GPU metrics
    try:
        gpu_data = hpc_gpu_nvidia_smi(node, cluster=cluster)
        summary["gpu"] = {
            "driver_version": gpu_data.get("driver_version", ""),
            "gpu_count": len(gpu_data.get("gpus", [])),
            "gpus": [
                {
                    "gpu_id": g.get("gpu_id"),
                    "product_name": g.get("product_name"),
                    "temperature": g.get("temperature"),
                    "gpu_util": g.get("gpu_util"),
                }
                for g in gpu_data.get("gpus", [])
            ],
        }
    except Exception:
        summary["gpu"]["error"] = "unreachable"

    # Fabric
    try:
        fabric_data = hpc_fabric_ib_link_status(node, cluster=cluster)
        summary["fabric"] = {
            "status": fabric_data.get("status", "unknown"),
            "links": fabric_data.get("links", []),
        }
    except Exception:
        summary["fabric"]["error"] = "unreachable"

    # XID errors
    with contextlib.suppress(Exception):
        summary["xid_errors"] = hpc_logs_dmesg_xid(node, cluster=cluster)

    return summary
