"""
HPC cluster management tools — subprocess wrappers for Slurm, Warewulf, Ansible, Spack.

Every function that mutates cluster state accepts dry_run=True (default False).
When dry_run is True the resolved command is returned as a string prefixed with
"DRY-RUN: " and no subprocess is executed.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
from typing import Any

from hpc_pilot.config import Config, load_config

# Module-level config — loaded once from ~/.hpc-pilot/config.yaml on first use.
_config: Config | None = None


def _cfg() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\[\],.-]*$")
_USER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _validate(value: str, field: str, pattern: re.Pattern[str] = _NAME_RE) -> None:
    """Raise ValueError if *value* is non-empty and does not match *pattern*."""
    if value and not pattern.match(value):
        raise ValueError(f"Invalid {field}: {value!r}")


# ---------------------------------------------------------------------------
# Subprocess helper
# ---------------------------------------------------------------------------


def _run(cmd: list[str], *, timeout: int = 60, dry_run: bool = False) -> str:
    """Run *cmd* and return stdout; raise RuntimeError on non-zero exit.

    When dry_run is True, return the shell-quoted command as a string without
    executing it.
    """
    if dry_run:
        return "DRY-RUN: " + " ".join(shlex.quote(c) for c in cmd)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            f"{cmd[0]} exited {result.returncode}: {stderr or '(no stderr)'}"
        )
    return result.stdout


# ---------------------------------------------------------------------------
# Availability probes (swallow all errors — caller gets a bool)
# ---------------------------------------------------------------------------


def check_slurm_available() -> bool:
    try:
        subprocess.run(
            [_cfg().slurm("scontrol"), "--version"], capture_output=True, check=True, timeout=5
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_warewulf_available() -> bool:
    try:
        subprocess.run(
            [_cfg().warewulf("wwctl"), "--version"], capture_output=True, check=True, timeout=5
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_spack_available() -> bool:
    try:
        subprocess.run(
            [_cfg().spack(), "--version"], capture_output=True, check=True, timeout=5
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_ansible_available() -> bool:
    try:
        subprocess.run(["ansible", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ---------------------------------------------------------------------------
# Slurm tools
# ---------------------------------------------------------------------------


def hpc_slurm_node_status(node: str = "") -> str:
    """Return scontrol node info for *node*, or all nodes when *node* is empty."""
    _validate(node, "node name")
    cmd = [_cfg().slurm("scontrol"), "show", "node"]
    if node:
        cmd.append(node)
    return _run(cmd, timeout=90)


def hpc_slurm_queue(filters: dict[str, str] | None = None) -> str:
    """Return squeue output, optionally filtered.

    Supported filter keys: ``user``, ``partition``, ``state``.
    """
    allowed_filters = {"user", "partition", "state"}
    cmd = [_cfg().slurm("squeue")]
    if filters:
        for key, value in filters.items():
            if key not in allowed_filters:
                raise ValueError(f"Unknown filter key: {key!r}")
            _validate(value, key, _USER_RE)
            cmd.extend([f"--{key.replace('_', '-')}", value])
    return _run(cmd)


def hpc_slurm_node_state(
    node: str,
    target: str,
    reason: str | None = None,
    dry_run: bool = False,
) -> str:
    """Change a Slurm node's state (drain / resume / down / undrain)."""
    _validate(node, "node name")
    allowed = {"drain", "resume", "down", "undrain"}
    if target not in allowed:
        raise ValueError(f"Invalid target state: {target!r}. Must be one of {sorted(allowed)}")
    cmd = [_cfg().slurm("scontrol"), "update", f"node={node}", f"state={target}"]
    if reason:
        cmd.append(f"reason={reason}")
    return _run(cmd, dry_run=dry_run)


def hpc_slurm_qos_modify(
    name: str,
    max_wall_min: int | None = None,
    dry_run: bool = False,
) -> str:
    """Modify a Slurm QOS entry.

    When dry_run is True the would-be sacctmgr command is returned without
    executing it.  Pass dry_run=False (and gate with --apply at the CLI) for
    real execution.
    """
    _validate(name, "QOS name", _USER_RE)
    cmd = [_cfg().slurm("sacctmgr"), "--immediate", "modify", "qos", name, "set"]
    if max_wall_min is not None:
        cmd.append(f"MaxWall={max_wall_min}")
    return _run(cmd, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Warewulf tools
# ---------------------------------------------------------------------------


def hpc_warewulf_node_status() -> str:
    """Return wwctl node list output."""
    return _run([_cfg().warewulf("wwctl"), "node", "list"])


def hpc_warewulf_image_list() -> str:
    """Return wwctl image list output."""
    return _run([_cfg().warewulf("wwctl"), "image", "list"])


def hpc_warewulf_power_reset(node: str, dry_run: bool = False) -> str:
    """Power-reset a Warewulf node so it PXE-boots from its assigned image."""
    _validate(node, "node name")
    return _run([_cfg().warewulf("wwctl"), "power", "reset", node], timeout=120, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Spack tools
# ---------------------------------------------------------------------------


def hpc_spack_env_list() -> str:
    """Return spack env list output."""
    return _run([_cfg().spack(), "env", "list"])


def hpc_spack_find(env: str) -> str:
    """Return installed specs in a Spack environment."""
    _validate(env, "environment name", _USER_RE)
    return _run([_cfg().spack(), "find", "-l", "-N", "-d", "-e", env], timeout=60)


def hpc_spack_compilers() -> str:
    """Return the list of available Spack compilers."""
    return _run([_cfg().spack(), "compilers"])


# ---------------------------------------------------------------------------
# Ansible tools
# ---------------------------------------------------------------------------


def hpc_ansible_playbook_run(
    playbook: str,
    limit: str | None = None,
    check: bool = False,
    dry_run: bool = False,
) -> str:
    """Run an Ansible playbook.

    When dry_run is True the resolved ansible-playbook command is returned
    without executing it.  *check* maps to ansible-playbook's --check flag.
    """
    if not playbook:
        raise ValueError("playbook path must not be empty")
    cmd = ["ansible-playbook", playbook]
    if limit:
        cmd.extend(["--limit", limit])
    if check:
        cmd.append("--check")
    return _run(cmd, timeout=600, dry_run=dry_run)


def hpc_ansible_inventory_generate() -> str:
    """Return an Ansible inventory snapshot from the local inventory plugin."""
    return _run(["ansible-inventory", "-i", "localhost,", "--list"])


# ---------------------------------------------------------------------------
# Cluster health check
# ---------------------------------------------------------------------------


def parse_slurm_nodes(output: str) -> dict[str, Any]:
    """Parse ``scontrol show node`` output into a mapping keyed by node name.

    scontrol emits multiple ``key=value`` pairs per line; this function
    tokenises every line with a regex so compound lines like

        NodeName=node01 Arch=x86_64 CoresPerSocket=12

    are handled correctly.
    """
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


def hpc_cluster_health_check() -> dict[str, Any]:
    """Run a comprehensive cluster health check across all installed components."""
    import datetime

    health: dict[str, Any] = {
        "timestamp": str(datetime.datetime.now()),
        "components": {},
        "overall": "healthy",
        "issues": [],
    }

    # --- Slurm ---
    slurm_ok = check_slurm_available()
    health["components"]["slurm"] = {
        "available": slurm_ok,
        "status": "unknown" if not slurm_ok else "checking",
    }
    if slurm_ok:
        try:
            output = _run([_cfg().slurm("scontrol"), "show", "nodes"], timeout=90)
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
    ww_ok = check_warewulf_available()
    health["components"]["warewulf"] = {
        "available": ww_ok,
        "status": "unknown" if not ww_ok else "checking",
    }
    if ww_ok:
        try:
            _run([_cfg().warewulf("wwctl"), "node", "list"])
            health["components"]["warewulf"]["status"] = "healthy"
        except Exception as exc:
            health["components"]["warewulf"]["status"] = "error"
            health["issues"].append(f"Warewulf check error: {exc}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"

    # --- Spack ---
    spack_ok = check_spack_available()
    health["components"]["spack"] = {
        "available": spack_ok,
        "status": "unknown" if not spack_ok else "checking",
    }
    if spack_ok:
        try:
            _run([_cfg().spack(), "env", "list"])
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
