"""Observability & metrics tools: Prometheus, GPU, storage, fabric, and log inspection."""
from __future__ import annotations

import contextlib
import json
import re
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any, cast

from hpc_pilot.paths import config_path
from hpc_pilot.tools._run import (
    _resolve_cluster,
    _run,
)
from hpc_pilot.tools._validation import _validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cluster_prometheus_url(cluster_name: str) -> str:
    """Read the Prometheus URL from the cluster's config.yaml."""
    import os.path
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
    # If cluster-specific observability config exists, use that; otherwise global.
    clusters_raw = data.get("clusters", {}) or {}
    cluster_cfg = clusters_raw.get(cluster_name, {}) or {}
    cluster_obs = cluster_cfg.get("observability", {}) or {}
    url = cluster_obs.get("prometheus", {}).get("url") or obs.get("prometheus", {}).get("url") or ""
    if not url:
        raise RuntimeError(
            f"Prometheus URL not configured for cluster {cluster_name!r} — "
            "set observability.prometheus.url in config.yaml"
        )
    return str(url)


def _build_ssh_cmd(node: str, cluster: Any, remote_cmd: list[str]) -> list[str]:
    """Build a local command list that SSHes to *node* and runs *remote_cmd*.

    Uses the cluster's SSH config for the controller.  Falls back to the
    node name directly when the cluster does not carry SSH settings.
    """
    if cluster is not None and cluster.ssh is not None:
        ssh = cluster.ssh
        import os
        cmd = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=10",
            "-i", os.path.expanduser(ssh.key),
            f"{ssh.user}@{node}",
            "--",
            *map(shlex.quote, remote_cmd),
        ]
        if ssh.control_path:
            cmd[1:1] = [
                "-o", f"ControlPath={ssh.control_path}",
                "-o", "ControlMaster=auto",
            ]
        return cmd
    # No SSH config — run the remote command via a plain ssh to the node
    return ["ssh", node, *map(shlex.quote, remote_cmd)]


# ---------------------------------------------------------------------------
# Redaction helper
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_SECRET_RE = re.compile(
    r"(?i)(password|token|secret|api_key|apikey|passwd)\s*[=:]\s*\S+"
)


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
    # Count line frequencies (strip whitespace for grouping)
    stripped = [ln.strip() for ln in redacted]
    top_n = Counter(stripped).most_common(5)
    summary_parts = [
        f"<output truncated: {len(lines)} lines, showing top-5 patterns>",
    ]
    for i, (pat, cnt) in enumerate(top_n):
        summary_parts.append(f"  [{i+1}] ({cnt}x) {pat[:120]}")
    return "\n".join(summary_parts)


# ---------------------------------------------------------------------------
# 1. Prometheus query
# ---------------------------------------------------------------------------

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

def hpc_metrics_prometheus_alerts(*, cluster: str = "default") -> list[dict[str, Any]]:
    """Fetch active alerts from Prometheus /api/v1/alerts."""
    base_url = _cluster_prometheus_url(cluster)
    url = f"{base_url.rstrip('/')}/api/v1/alerts"
    try:
        resp = urllib.request.urlopen(url, timeout=30)
        body = resp.read().decode("utf-8")
        data: dict[str, Any] = cast(dict[str, Any], json.loads(body))
        alerts: list[dict[str, Any]] = cast(list[dict[str, Any]], data.get("data", {}).get("alerts", []))
        return alerts
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Prometheus alerts HTTP {exc.code}: {body[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Prometheus alerts unreachable: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# 3. GPU: nvidia-smi
# ---------------------------------------------------------------------------

def hpc_gpu_nvidia_smi(
    node: str,
    *,
    cluster: str = "default",
) -> dict[str, Any]:
    """Run nvidia-smi -q -x on a node via SSH and return per-GPU metrics."""
    _validate(node, "node")
    cl = _resolve_cluster(cluster)
    remote_cmd = ["nvidia-smi", "-q", "-x"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)
    output = _run(cmd, timeout=120)

    result: dict[str, Any] = {
        "node": node,
        "gpus": [],
        "driver_version": "",
        "cuda_version": "",
    }

    try:
        root = ET.fromstring(output)
    except ET.ParseError as exc:
        result["error"] = f"Failed to parse nvidia-smi XML output: {exc}"
        return result

    # Driver / CUDA version from root attributes
    driver = root.attrib.get("driver_version", "")
    cuda_ver = ""
    cuda_elem = root.find("cuda_version")
    if cuda_elem is not None and cuda_elem.text:
        cuda_ver = cuda_elem.text.strip()
    result["driver_version"] = driver
    result["cuda_version"] = cuda_ver

    for gpu_elem in root.findall("gpu"):
        gpu_id = gpu_elem.attrib.get("id", "?")
        product = ""
        pn = gpu_elem.find("product_name")
        if pn is not None and pn.text:
            product = pn.text.strip()

        # Temperature
        temp = ""
        temp_elem = gpu_elem.find("temperature/gpu_temp")
        if temp_elem is not None and temp_elem.text:
            temp = temp_elem.text.strip()

        # GPU utilization
        util = ""
        util_elem = gpu_elem.find("utilization/gpu_util")
        if util_elem is not None and util_elem.text:
            util = util_elem.text.strip()

        # ECC errors
        ecc_errors: dict[str, Any] = {
            "volatile_single_bit": "N/A",
            "volatile_double_bit": "N/A",
            "aggregate_single_bit": "N/A",
            "aggregate_double_bit": "N/A",
        }
        ecc = gpu_elem.find("ecc_errors")
        if ecc is not None:
            for category in ("volatile", "aggregate"):
                cat_elem = ecc.find(category)
                if cat_elem is not None:
                    for label, key in [("single_bit", "single_bit"), ("double_bit", "double_bit")]:
                        elem = cat_elem.find(label)
                        if elem is not None and elem.text:
                            ecc_errors[f"{category}_{key}"] = elem.text.strip()

        result["gpus"].append({
            "gpu_id": gpu_id,
            "product_name": product,
            "temperature": temp,
            "gpu_util": util,
            "ecc_errors": ecc_errors,
        })

    return result


# ---------------------------------------------------------------------------
# 4. GPU: DCGM diagnostic
# ---------------------------------------------------------------------------

def hpc_gpu_dcgm_diag(
    node: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Run dcgmi diag -r 1 on a node via SSH."""
    _validate(node, "node")
    cl = _resolve_cluster(cluster)
    remote_cmd = ["dcgmi", "diag", "-r", "1"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)
    return _run(cmd, timeout=300, dry_run=dry_run)


# ---------------------------------------------------------------------------
# 5. Storage: Lustre status
# ---------------------------------------------------------------------------

def hpc_storage_lustre_status(*, cluster: str = "default") -> dict[str, Any]:
    """Check Lustre OST/MDT state via lctl get_param on the controller."""
    cl = _resolve_cluster(cluster)
    result: dict[str, Any] = {
        "osts": {},
        "mdts": {},
        "status": "unknown",
        "error": None,
    }

    # Check OSS / OST targets
    try:
        ost_output = _run(
            [cl.slurm("lctl"), "get_param", "obdfilter.*.state"],
            cluster=cl, timeout=30,
        )
        for line in ost_output.strip().splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                result["osts"][key.strip()] = val.strip()
    except Exception as exc:
        result["error"] = str(exc)

    # Check MDT targets
    try:
        mdt_output = _run(
            [cl.slurm("lctl"), "get_param", "mdt.*.state"],
            cluster=cl, timeout=30,
        )
        for line in mdt_output.strip().splitlines():
            if "=" in line:
                key, val = line.split("=", 1)
                result["mdts"][key.strip()] = val.strip()
    except Exception:
        pass  # MDT check is optional

    # Determine overall status
    if result["error"]:
        result["status"] = "error"
    elif not result["osts"]:
        result["status"] = "not_available"
    else:
        all_ok = all(
            v.lower() in ("online", "active") for v in result["osts"].values()
        )
        result["status"] = "healthy" if all_ok else "degraded"

    return result


# ---------------------------------------------------------------------------
# 6. Storage: mounts
# ---------------------------------------------------------------------------

_MOUNT_LINE_RE = re.compile(
    r"^(\S+) on (\S+) type (\S+)"
)
_DF_LINE_RE = re.compile(
    r"^(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+.*)$"
)


def hpc_storage_mounts(*, cluster: str = "default") -> dict[str, Any]:
    """Inspect current mounts and disk usage on the controller."""
    cl = _resolve_cluster(cluster)
    result: dict[str, Any] = {
        "mounts": [],
        "disk_usage": [],
    }

    # mount output
    try:
        mount_out = _run(["mount"], cluster=cl, timeout=15)
        for line in mount_out.strip().splitlines():
            m = _MOUNT_LINE_RE.match(line)
            if m:
                result["mounts"].append({
                    "device": m.group(1),
                    "mount_point": m.group(2),
                    "filesystem": m.group(3),
                })
    except Exception:
        pass

    # df -h output
    try:
        df_out = _run(["df", "-h"], cluster=cl, timeout=15)
        lines = df_out.strip().splitlines()
        if lines:
            for line in lines[1:]:  # skip header
                m = _DF_LINE_RE.match(line)
                if m:
                    result["disk_usage"].append({
                        "filesystem": m.group(1),
                        "size": m.group(2),
                        "used": m.group(3),
                        "available": m.group(4),
                        "use_percent": m.group(5),
                        "mounted_on": m.group(6).strip(),
                    })
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# 7. Fabric: IB link status
# ---------------------------------------------------------------------------

def hpc_fabric_ib_link_status(
    node: str,
    *,
    cluster: str = "default",
) -> dict[str, Any]:
    """Run ibstatus on a node via SSH and parse link state/rate/errors."""
    _validate(node, "node")
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
        # e.g. "Infiniband device 'mlx5_0' port 1 status:"
        m = re.match(
            r"Infiniband device '(\S+)' port (\d+)", line
        )
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

    # Determine overall status
    states = {link.get("state", "").lower() for link in result["links"]}
    if not result["links"]:
        result["status"] = "no_interfaces"
    elif "active" in states or "up" in states:
        result["status"] = "healthy" if "down" not in states else "degraded"
    else:
        result["status"] = "down"

    return result


# ---------------------------------------------------------------------------
# 8. Logs: slurmctld tail
# ---------------------------------------------------------------------------

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
        try:
            tail = subprocess.run(
                ["tail", "-n", str(lines), "/var/log/slurm/slurmctld.log"],
                capture_output=True, text=True, timeout=30,
            )
            if tail.returncode == 0:
                grep_proc = subprocess.run(
                    ["grep", "-E", "--", grep],
                    input=tail.stdout, capture_output=True, text=True, timeout=30,
                )
                output = _redact_output(grep_proc.stdout)
                return output
            else:
                raise RuntimeError(f"tail exited {tail.returncode}: {tail.stderr.strip()}")
        except FileNotFoundError:
            raise RuntimeError("tail or grep not found on the controller") from None
    else:
        cmd = ["tail", "-n", str(lines), "/var/log/slurm/slurmctld.log"]

    output = _run(cmd, cluster=cl, timeout=30)
    return _redact_output(output)


# ---------------------------------------------------------------------------
# 9. Logs: slurmd tail on a node
# ---------------------------------------------------------------------------

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

def hpc_logs_dmesg_xid(
    node: str,
    *,
    cluster: str = "default",
) -> list[dict[str, str]]:
    """Search dmesg for GPU XID errors on a node via SSH."""
    _validate(node, "node")
    cl = _resolve_cluster(cluster)
    # Pipe via shell on the remote node so dmesg | grep works over SSH
    import shlex
    remote_cmd = ["sh", "-c", "dmesg | grep -i xid"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)

    try:
        output = _run(cmd, timeout=60)
    except RuntimeError:
        return []

    results: list[dict[str, str]] = []
    for line in output.splitlines():
        ts = ""
        # Try to extract timestamp: "[12345.678901]"
        ts_match = re.match(r"\[(\d+\.\d+)\]", line)
        if ts_match:
            ts = ts_match.group(1)
        results.append({
            "timestamp": ts,
            "message": line.strip(),
        })
    return results


# ---------------------------------------------------------------------------
# 11. Logs: search journald on controller
# ---------------------------------------------------------------------------

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
        journal = subprocess.run(
            ["journalctl", f"--since={since}", "--no-pager"],
            capture_output=True, text=True, timeout=60,
        )
        if journal.returncode == 0:
            grep_proc = subprocess.run(
                ["grep", "-E", "--", pattern],
                input=journal.stdout, capture_output=True, text=True, timeout=60,
            )
            output = _redact_output(grep_proc.stdout)
            return output if output.strip() else "(no matching lines)"
        else:
            raise RuntimeError(f"journalctl exited {journal.returncode}")
    except FileNotFoundError:
        raise RuntimeError("journalctl or grep not found on the controller") from None


# ---------------------------------------------------------------------------
# Node summary — aggregates key metrics for one node
# ---------------------------------------------------------------------------


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
