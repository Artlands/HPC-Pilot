"""Ansible tools."""

from __future__ import annotations

import glob
import json
import os
import shlex
import subprocess
import time
from typing import Any

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _try_import_jobs() -> Any:
    """Lazy import hpc_pilot.jobs — returns None if it doesn't exist."""
    try:
        from hpc_pilot import jobs  # noqa: F401

        return jobs
    except (ImportError, ModuleNotFoundError):
        return None


# ---------------------------------------------------------------------------
# A.1 — hpc_ansible_playbook_check  (ADMIN)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_playbook_check",
    role=Role.ADMIN,
    schema={
        "name": "hpc_ansible_playbook_check",
        "description": "Run ansible-playbook --check --diff to preview changes without applying them. Returns per-host structured diff output (JSON). Use dry_run=true to preview the command without executing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "playbook": {
                    "type": "string",
                    "description": "Absolute path to the YAML playbook file",
                },
                "limit": {
                    "type": "string",
                    "description": "Ansible host limit pattern (e.g. 'gpu_nodes')",
                },
            },
            "required": ["playbook"],
        },
    },
)
def hpc_ansible_playbook_check(
    playbook: str,
    limit: str | None = None,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run ansible-playbook --check --diff and return per-host structured diff."""
    cl = _resolve_cluster(cluster)

    if dry_run:
        cmd_parts = ["ansible-playbook", "--check", "--diff", playbook]
        if limit:
            cmd_parts.extend(["--limit", limit])
        return {"dry_run": "DRY-RUN: " + " ".join(shlex.quote(c) for c in cmd_parts)}

    env = os.environ.copy()
    env["ANSIBLE_STDOUT_CALLBACK"] = "json"

    cmd_parts = [cl.ansible_playbook(), "--check", "--diff", playbook]
    if limit:
        cmd_parts.extend(["--limit", limit])

    output = _run(cmd_parts, cluster=cl, timeout=600, env=env)

    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        # Fallback: wrap raw output
        return {"raw_output": output.strip(), "parsed": False}

    # Per-host structured diff
    host_stats: dict[str, Any] = data.get("stats", {}) if isinstance(data, dict) else {}
    return {
        "playbook": playbook,
        "limit": limit,
        "check_mode": True,
        "plays": data.get("plays", []) if isinstance(data, dict) else [],
        "stats": host_stats,
    }


# ---------------------------------------------------------------------------
# A.2 — hpc_ansible_playbook_list  (VIEWER)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_playbook_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_ansible_playbook_list",
        "description": "List all Ansible playbooks in the cluster's playbook directory with metadata.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_ansible_playbook_list(*, cluster: str = "default") -> list[dict[str, Any]]:
    """Enumerate Ansible playbooks with header metadata."""
    cl = _resolve_cluster(cluster)
    playbooks_dir = os.path.join(cl.ansible_dir, "playbooks")
    if not os.path.isdir(playbooks_dir):
        return []

    results: list[dict[str, Any]] = []
    for path in sorted(glob.glob(os.path.join(playbooks_dir, "*.yml"))):
        name = os.path.splitext(os.path.basename(path))[0]
        description = ""
        try:
            with open(path) as f:
                first_line = f.readline().strip()
                if first_line.startswith("#"):
                    description = first_line.lstrip("#").strip()
        except OSError:
            pass
        results.append({"path": path, "name": name, "description": description})
    return results


# ---------------------------------------------------------------------------
# A.3 — hpc_ansible_role_list  (VIEWER)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_role_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_ansible_role_list",
        "description": "List all Ansible role directories on the cluster.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_ansible_role_list(*, cluster: str = "default") -> list[str]:
    """List Ansible role directories."""
    cl = _resolve_cluster(cluster)
    roles_dir = os.path.join(cl.ansible_dir, "roles")
    if not os.path.isdir(roles_dir):
        return []

    return sorted(
        d
        for d in os.listdir(roles_dir)
        if os.path.isdir(os.path.join(roles_dir, d)) and not d.startswith(".")
    )


# ---------------------------------------------------------------------------
# A.4 — hpc_ansible_inventory_from_truth  (ADMIN)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_inventory_from_truth",
    role=Role.ADMIN,
    schema={
        "name": "hpc_ansible_inventory_from_truth",
        "description": "Build an Ansible inventory YAML from Warewulf and Slurm source of truth. Queries wwctl node list and scontrol show nodes, then writes a YAML inventory with groups for gpu_nodes, cpu_nodes, and partitions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_ansible_inventory_from_truth(*, cluster: str = "default") -> dict[str, Any]:
    """Build Ansible inventory from Warewulf + Slurm source of truth."""

    cl = _resolve_cluster(cluster)

    ts = time.strftime("%Y-%m-%d %H:%M:%S %Z")

    # Query Warewulf nodes via subprocess (simulating hpc_warewulf_node_status)
    ww_output = ""
    scontrol_output = ""

    try:
        ww_output = _run([cl.warewulf("wwctl"), "node", "list"], cluster=cl, timeout=30)
    except Exception:
        pass

    # Query Slurm nodes
    try:
        scontrol_output = _run([cl.slurm("scontrol"), "show", "nodes"], cluster=cl, timeout=30)
    except Exception:
        pass

    # Parse hostnames from wwctl output (skip header line)
    ww_hosts: list[str] = []
    for line in ww_output.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("NODE") or line.startswith("---"):
            continue
        parts = line.split()
        if parts:
            ww_hosts.append(parts[0])

    # Parse Slurm node features
    gpu_nodes: list[str] = []
    cpu_nodes: list[str] = []
    partition_map: dict[str, list[str]] = {}

    # Parse scontrol show nodes — each node block starts with "NodeName="
    current_node = ""
    for line in scontrol_output.splitlines():
        line = line.strip()
        if line.startswith("NodeName="):
            # Extract node name
            current_node = ""
            for part in line.split():
                if part.startswith("NodeName="):
                    current_node = part.split("=", 1)[1]
                    break
        if current_node:
            for part in line.split():
                if part.startswith("Features="):
                    features_raw = part.split("=", 1)[1].strip()
                    features = [f.strip().lower() for f in features_raw.split(",")]
                    if "gpu" in features:
                        if current_node not in gpu_nodes:
                            gpu_nodes.append(current_node)
                    elif current_node not in cpu_nodes:
                        cpu_nodes.append(current_node)
                if part.startswith("Partitions="):
                    partitions_raw = part.split("=", 1)[1].strip()
                    for p in partitions_raw.split(","):
                        p = p.strip()
                        if p:
                            partition_map.setdefault(p, [])
                            if current_node not in partition_map[p]:
                                partition_map[p].append(current_node)

    # Build inventory YAML
    # We generate YAML manually to ensure clean formatting
    yaml_lines: list[str] = [
        "# generated by HPC Pilot at " + ts + "; do not edit",
        "---",
        "all:",
        "  hosts:",
    ]

    all_hosts = sorted(set(ww_hosts) | set(gpu_nodes) | set(cpu_nodes))
    if not all_hosts:
        all_hosts = ww_hosts or sorted(set(gpu_nodes) | set(cpu_nodes))

    for host in all_hosts:
        yaml_lines.append(f"    {host}:")

    # Groups based on features
    yaml_lines.append("  children:")
    yaml_lines.append("    gpu_nodes:")
    yaml_lines.append("      hosts:")
    for host in sorted(gpu_nodes):
        yaml_lines.append(f"        {host}:")
    yaml_lines.append("    cpu_nodes:")
    yaml_lines.append("      hosts:")
    for host in sorted(cpu_nodes):
        yaml_lines.append(f"        {host}:")

    # Partition groups
    for partition in sorted(partition_map):
        group_name = f"partition_{partition}"
        yaml_lines.append(f"    {group_name}:")
        yaml_lines.append("      hosts:")
        for host in sorted(partition_map[partition]):
            yaml_lines.append(f"        {host}:")

    new_inventory = "\n".join(yaml_lines) + "\n"

    # Write to ansible dir
    inventory_dir = os.path.join(cl.ansible_dir, "inventory")
    os.makedirs(inventory_dir, exist_ok=True)
    target_path = os.path.join(inventory_dir, "generated.yml")

    prev_content = ""
    if os.path.exists(target_path):
        try:
            with open(target_path) as f:
                prev_content = f.read()
        except OSError:
            pass

    with open(target_path, "w") as f:
        f.write(new_inventory)

    diff_lines: list[str] = []
    if prev_content:
        old_lines = prev_content.splitlines(keepends=True)
        new_lines = new_inventory.splitlines(keepends=True)

        # Produce a simple diff
        import difflib

        for line in difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="previous",
            tofile="current",
        ):
            diff_lines.append(line.rstrip())
    else:
        # First run — show full inventory
        diff_lines = new_inventory.splitlines()

    return {
        "inventory_path": target_path,
        "gpu_nodes": sorted(gpu_nodes),
        "cpu_nodes": sorted(cpu_nodes),
        "partitions": {k: sorted(v) for k, v in sorted(partition_map.items())},
        "diff": diff_lines,
    }


# ---------------------------------------------------------------------------
# A.5 — hpc_ansible_drift_check  (OPERATOR)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_drift_check",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_ansible_drift_check",
        "description": "Run curated drift-check playbooks to detect configuration drift. Checks available: slurm-config, chrony-sync, mount, kernel-version. Pass which='all' (default) to run all, or specify a single check (e.g. 'mount').",
        "input_schema": {
            "type": "object",
            "properties": {
                "which": {
                    "type": "string",
                    "description": "Which drift check to run ('all' or specific check name)",
                }
            },
            "required": [],
        },
    },
)
def hpc_ansible_drift_check(
    which: str = "all",
    *,
    cluster: str = "default",
) -> dict[str, Any]:
    """Run curated drift-check playbooks and return per-host results."""
    cl = _resolve_cluster(cluster)

    # Drift playbooks directory (within the project)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    drift_dir = os.path.join(project_root, "playbooks", "drift")

    if not os.path.isdir(drift_dir):
        return {
            "error": "No drift playbooks directory found",
            "path": drift_dir,
            "results": [],
        }

    playbook_map: dict[str, str] = {}
    for fname in sorted(glob.glob(os.path.join(drift_dir, "*.yml"))):
        base = os.path.basename(fname)
        key = base.replace("-drift.yml", "").replace(".yml", "")
        playbook_map[key] = fname

    keys_to_run: list[str] = []
    if which == "all":
        keys_to_run = sorted(playbook_map.keys())
    elif which in playbook_map:
        keys_to_run = [which]
    elif which.endswith("-drift.yml") or which.endswith(".yml"):
        full_path = os.path.join(drift_dir, which) if not os.path.isabs(which) else which
        abs_path = os.path.abspath(full_path)
        if os.path.exists(abs_path):
            keys_to_run = [os.path.basename(abs_path)]
        else:
            return {"error": f"Playbook not found: {abs_path}", "results": []}
    else:
        return {"error": f"Unknown drift check: {which!r}", "results": []}

    env = os.environ.copy()
    env["ANSIBLE_STDOUT_CALLBACK"] = "json"

    results: list[dict[str, Any]] = []
    for key in keys_to_run:
        pb_path = playbook_map.get(key)
        if not pb_path:
            # Handle full path case
            pb_path = key
        cmd_parts = [
            cl.ansible_playbook(),
            "--check",
            "--diff",
            pb_path,
        ]
        try:
            stdout = _run(cmd_parts, cluster=cl, timeout=300, env=env)
            pb_data: dict[str, Any] = {"check": key, "playbook": pb_path}
            try:
                parsed = json.loads(stdout)
                pb_data["plays"] = parsed.get("plays", [])
                pb_data["stats"] = parsed.get("stats", {})
            except json.JSONDecodeError:
                pb_data["raw_output"] = stdout.strip()
            results.append(pb_data)
        except RuntimeError as exc:
            results.append({"check": key, "playbook": pb_path, "error": str(exc)})

    return {"drift_dir": drift_dir, "results": results}


# ---------------------------------------------------------------------------
# A.6 — hpc_ansible_vault_decrypt  (ADMIN)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_vault_decrypt",
    role=Role.ADMIN,
    schema={
        "name": "hpc_ansible_vault_decrypt",
        "description": "Decrypt and view an Ansible Vault file. Content is never logged to the audit trail. Use dry_run=true to preview the path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the encrypted Ansible vault file",
                }
            },
            "required": ["path"],
        },
    },
)
def hpc_ansible_vault_decrypt(
    path: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Decrypt and return an Ansible vault file (content is NOT logged)."""
    cl = _resolve_cluster(cluster)

    if dry_run:
        return f"DRY-RUN: ansible-vault view {path}"

    cmd = [cl.ansible_playbook().replace("ansible-playbook", "ansible-vault"), "view", path]
    return _run(cmd, cluster=cl, timeout=60)


# ---------------------------------------------------------------------------
# A.7 — hpc_ansible_run_history  (VIEWER)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_run_history",
    role=Role.VIEWER,
    schema={
        "name": "hpc_ansible_run_history",
        "description": "Show the history of past Ansible playbook runs from the run log.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_ansible_run_history(*, cluster: str = "default") -> list[dict[str, Any]]:
    """Read past Ansible run records from ~/.hpc-pilot/logs/ansible/.json."""
    from hpc_pilot.paths import get_home

    logs_dir = os.path.join(get_home(), "logs", "ansible")
    if not os.path.isdir(logs_dir):
        return []

    records: list[dict[str, Any]] = []
    for fname in sorted(os.listdir(logs_dir)):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(logs_dir, fname)
        try:
            with open(fpath) as f:
                data = json.load(f)
            records.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    records.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return records


# ---------------------------------------------------------------------------
# B — hpc_ansible_playbook_run (refactored async)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_playbook_run",
    role=Role.ADMIN,
    schema={
        "name": "hpc_ansible_playbook_run",
        "description": "Run an Ansible playbook against cluster nodes. Pass check=true to do a Ansible dry-run (--check). Pass dry_run=true to preview the ansible-playbook command without executing at all.",
        "input_schema": {
            "type": "object",
            "properties": {
                "playbook": {
                    "type": "string",
                    "description": "Absolute path to the YAML playbook file",
                },
                "limit": {
                    "type": "string",
                    "description": "Ansible host limit pattern (e.g. 'gpu_nodes')",
                },
                "check": {
                    "type": "boolean",
                    "description": "Pass --check to ansible-playbook (no changes on hosts)",
                },
            },
            "required": ["playbook"],
        },
    },
)
def hpc_ansible_playbook_run(
    playbook: str,
    limit: str | None = None,
    check: bool = False,
    dry_run: bool = False,
    *,
    cluster: str = "default",
) -> str | dict[str, Any]:
    """Run an Ansible playbook (async via jobs if available, sync otherwise)."""
    import shlex

    if not playbook:
        raise ValueError("playbook path must not be empty")

    cl = _resolve_cluster(cluster)
    cmd = [cl.ansible_playbook(), playbook]
    if limit:
        cmd.extend(["--limit", limit])
    if check:
        cmd.append("--check")

    if dry_run:
        return "DRY-RUN: " + " ".join(shlex.quote(c) for c in cmd)

    jobs_mod = _try_import_jobs()
    if jobs_mod is not None:
        # Async via job infrastructure
        record = jobs_mod.start_job(
            cmd=cmd,
            meta={"tool": "hpc_ansible_playbook_run", "cluster": cluster, "playbook": playbook},
        )
        return {"run_id": record.run_id}

    # Fallback: synchronous
    return _run(cmd, cluster=cl, timeout=600, dry_run=False)


# ---------------------------------------------------------------------------
# Existing — hpc_ansible_inventory_generate  (VIEWER)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_ansible_inventory_generate",
    role=Role.VIEWER,
    schema={
        "name": "hpc_ansible_inventory_generate",
        "description": "Generate and display the current Ansible inventory.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_ansible_inventory_generate(*, cluster: str = "default") -> str:
    """Return an Ansible inventory snapshot from the local inventory plugin."""
    cl = _resolve_cluster(cluster)
    return _run([cl.ansible_inventory(), "-i", "localhost,", "--list"], cluster=cl)
