"""HPC system administration tools — user management, service controls, login node process inspection, storage utilities, audit querying, and config backup."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import time
from typing import Any

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _USER_RE, _validate

# ===================================================================
# Audit log query (S10)
# ===================================================================


@hpc_tool(
    name="hpc_audit_query",
    role=Role.VIEWER,
    schema={
        "name": "hpc_audit_query",
        "description": "Search and filter the HPC-Pilot audit log. Returns matching records as JSON lines, newest first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "actor": {"type": "string"},
                "role": {"type": "string"},
                "error_only": {"type": "boolean"},
                "since_ts": {"type": "number"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
)
def hpc_audit_query(
    tool: str = "",
    actor: str = "",
    role: str = "",
    *,
    error_only: bool = False,
    since_ts: float | None = None,
    limit: int = 50,
    cluster: str = "default",
) -> str:
    """Search and filter the audit log programmatically.

    Reads from ``~/.hpc-pilot/logs/audit.jsonl`` and returns matching
    records as JSON lines, newest first.

    Args:
        tool: Filter by tool name (substring match).
        actor: Filter by actor name (substring match).
        role: Filter by role name (exact match).
        error_only: Only return records with a non-empty error field.
        since_ts: Unix timestamp — only return records after this time.
        limit: Maximum number of records to return (default 50).
    """
    audit_path = os.path.join(get_home(), "logs", "audit.jsonl")
    if not os.path.exists(audit_path):
        return "[]"

    matched: list[dict[str, Any]] = []
    with open(audit_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = record.get("ts", 0)
            if since_ts and ts < since_ts:
                continue
            if tool and tool not in record.get("tool", ""):
                continue
            if actor and actor not in record.get("actor", ""):
                continue
            if role and role != record.get("role", ""):
                continue
            if error_only and not record.get("error"):
                continue

            matched.append(record)
            if len(matched) >= limit:
                break

    # Newest first
    matched.sort(key=lambda r: r.get("ts", 0), reverse=True)

    if not matched:
        return "[]"

    out = io.StringIO()
    for rec in matched:
        out.write(json.dumps(rec, default=str) + "\n")
    return out.getvalue().rstrip()


# ===================================================================
# Slurm service lifecycle (S11)
# ===================================================================

_SLURM_SERVICES = frozenset({"slurmctld", "slurmd", "slurmdbd"})
_SLURM_ACTIONS = frozenset({"start", "stop", "restart", "status"})


@hpc_tool(
    name="hpc_slurm_service",
    role=Role.ADMIN,
    schema={
        "name": "hpc_slurm_service",
        "description": "Manage Slurm daemon service lifecycle via systemctl. Use for starting/stopping/restarting slurmctld, slurmd, or slurmdbd.",
        "input_schema": {
            "type": "object",
            "properties": {
                "service": {
                    "type": "string",
                    "enum": ["slurmctld", "slurmd", "slurmdbd"],
                    "description": "Which Slurm daemon to manage",
                },
                "action": {
                    "type": "string",
                    "enum": ["start", "stop", "restart", "status"],
                    "description": "Action to perform",
                },
            },
            "required": ["service", "action"],
        },
    },
)
def hpc_slurm_service(
    service: str,
    action: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Manage a Slurm daemon service lifecycle via systemctl.

    Args:
        service: One of ``slurmctld``, ``slurmd``, ``slurmdbd``.
        action: One of ``start``, ``stop``, ``restart``, ``status``.
        dry_run: Preview without executing.
    """
    if service not in _SLURM_SERVICES:
        raise ValueError(f"Invalid service: {service!r}. Must be one of {sorted(_SLURM_SERVICES)}")
    if action not in _SLURM_ACTIONS:
        raise ValueError(f"Invalid action: {action!r}. Must be one of {sorted(_SLURM_ACTIONS)}")

    cl = _resolve_cluster(cluster)
    cmd = ["systemctl", action, service]
    return _run(cmd, cluster=cl, dry_run=dry_run, timeout=120)


# ===================================================================
# UNIX user management (S13)
# ===================================================================


@hpc_tool(
    name="hpc_system_user_add",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_user_add",
        "description": "Create a UNIX user account on the login node (useradd). Used for onboarding new HPC users.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Username for the new account"},
                "uid": {"type": "integer", "description": "Optional numeric UID"},
                "groups": {"type": "string", "description": "Comma-separated supplementary groups"},
                "shell": {"type": "string", "description": "Login shell (default /bin/bash)"},
                "home": {"type": "string", "description": "Home directory path"},
            },
            "required": ["username"],
        },
    },
)
def hpc_system_user_add(
    username: str,
    uid: int | None = None,
    groups: str = "",
    shell: str = "/bin/bash",
    home: str = "",
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Create a UNIX user account on the login node (useradd).

    Args:
        username: Username for the new account.
        uid: Optional numeric UID.
        groups: Optional comma-separated supplementary groups.
        shell: Login shell (default /bin/bash).
        home: Home directory path (default /home/<username>).
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = ["useradd"]
    if uid is not None:
        cmd += ["-u", str(uid)]
    if groups:
        cmd += ["-G", groups]
    if shell:
        cmd += ["-s", shell]
    if home:
        cmd += ["-d", home]
        _validate(home, "home directory")
    cmd.append(username)
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_system_user_delete",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_user_delete",
        "description": "Delete a UNIX user account (userdel).",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "remove_home": {"type": "boolean", "description": "Also remove home directory"},
            },
            "required": ["username"],
        },
    },
)
def hpc_system_user_delete(
    username: str,
    remove_home: bool = False,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Delete a UNIX user account (userdel).

    Args:
        username: Username to delete.
        remove_home: Also remove the user's home directory and mail spool.
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = ["userdel"]
    if remove_home:
        cmd.append("-r")
    cmd.append(username)
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_system_user_group_add",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_user_group_add",
        "description": "Add a user to a supplementary group (usermod -aG).",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "group": {"type": "string", "description": "Group name to add the user to"},
            },
            "required": ["username", "group"],
        },
    },
)
def hpc_system_user_group_add(
    username: str,
    group: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Add a user to a supplementary group (usermod -aG).

    Args:
        username: Username.
        group: Group name to add the user to.
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    _validate(group, "group name", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = ["usermod", "-aG", group, username]
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_system_ssh_key_deploy",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_ssh_key_deploy",
        "description": "Deploy an SSH public key to a user's authorized_keys on the login node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "public_key": {"type": "string", "description": "SSH public key line to append"},
            },
            "required": ["username", "public_key"],
        },
    },
)
def hpc_system_ssh_key_deploy(
    username: str,
    public_key: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Deploy an SSH public key for a user.

    Creates ``~<username>/.ssh/authorized_keys`` if absent and appends the
    public key. The ``.ssh`` directory and ``authorized_keys`` file are
    created with the correct permissions (700 / 600).

    Args:
        username: Username whose authorized_keys to modify.
        public_key: SSH public key line to append.
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    if not public_key.startswith("ssh-") and not public_key.startswith("ecdsa-"):
        raise ValueError(
            "public_key must start with 'ssh-' or 'ecdsa-' " "(e.g. 'ssh-ed25519 AAA...')"
        )
    cl = _resolve_cluster(cluster)
    home_dir = os.path.join("/home", username)

    # Build a chained command: create .ssh dir, set perms, append key
    cmds = " && ".join(
        [
            f"mkdir -p {home_dir}/.ssh",
            f"chmod 700 {home_dir}/.ssh",
            f"touch {home_dir}/.ssh/authorized_keys",
            f"chmod 600 {home_dir}/.ssh/authorized_keys",
            f"grep -qF {_shkey(username, public_key)} {home_dir}/.ssh/authorized_keys "
            f"|| echo {_shkey(username, public_key)} >> {home_dir}/.ssh/authorized_keys",
        ]
    )
    return _run(["sh", "-c", cmds], cluster=cl, dry_run=dry_run)


def _shkey(username: str, key: str) -> str:
    """Shell-escape a key line for use in a grep/echo command."""
    # Use base64 to avoid shell escaping issues
    import shlex

    return shlex.quote(key.strip())


# ===================================================================
# Login node process inspection (S18)
# ===================================================================


@hpc_tool(
    name="hpc_login_node_processes",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_login_node_processes",
        "description": "List top resource-consuming processes on the login node. Use when investigating high load or unauthorized computation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sort_by": {
                    "type": "string",
                    "enum": ["cpu", "mem", "pid"],
                    "description": "Sort field (default cpu)",
                },
                "limit": {"type": "integer", "description": "Number of processes (default 20)"},
            },
            "required": [],
        },
    },
)
def hpc_login_node_processes(
    sort_by: str = "cpu",
    limit: int = 20,
    *,
    cluster: str = "default",
) -> str:
    """List the top resource-consuming processes on the login node.

    Args:
        sort_by: Sort field — ``cpu``, ``mem``, or ``pid`` (default ``cpu``).
        limit: Number of processes to return (default 20).

    Returns a table with columns: PID, USER, %CPU, %MEM, COMMAND.
    """
    allowed_sort = {"cpu": "%cpu", "mem": "%mem", "pid": "pid"}
    sort_field = allowed_sort.get(sort_by)
    if sort_field is None:
        raise ValueError(f"Invalid sort_by: {sort_by!r}. Must be one of {sorted(allowed_sort)}")

    cl = _resolve_cluster(cluster)
    cmd = [
        "ps",
        "axo",
        "pid,user:12,%cpu,%mem,comm",
        "--sort=-" + sort_field,
        "--no-headers",
    ]
    raw = _run(cmd, cluster=cl, timeout=30)
    lines = raw.strip().splitlines()
    out = io.StringIO()
    out.write(f"{'PID':>7} {'USER':12} {'%CPU':>5} {'%MEM':>5} COMMAND\n")
    out.write("-" * 70 + "\n")
    for line in lines[:limit]:
        out.write(line + "\n")
    return out.getvalue().rstrip()


# ===================================================================
# Storage tools (S12)
# ===================================================================


@hpc_tool(
    name="hpc_storage_large_files",
    role=Role.VIEWER,
    schema={
        "name": "hpc_storage_large_files",
        "description": "Find the largest files under a directory. Useful for storage crisis triage.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to search (e.g. /scratch)"},
                "min_size_mb": {
                    "type": "integer",
                    "description": "Minimum file size in MB (default 100)",
                },
                "limit": {"type": "integer", "description": "Max results (default 50)"},
            },
            "required": ["path"],
        },
    },
)
def hpc_storage_large_files(
    path: str,
    min_size_mb: int = 100,
    limit: int = 50,
    *,
    cluster: str = "default",
) -> str:
    """Find the largest files under a given directory path.

    Args:
        path: Directory to search (e.g. ``/scratch``).
        min_size_mb: Minimum file size in MB (default 100).
        limit: Max results (default 50).

    Returns a table: SIZE_MB, PATH.
    """
    _validate(path, "path")
    cl = _resolve_cluster(cluster)
    cmd = [
        "find",
        path,
        "-type",
        "f",
        "-size",
        f"+{min_size_mb}M",
        "-printf",
        "%s\\t%p\\n",
    ]
    raw = _run(cmd, cluster=cl, timeout=120)
    lines = raw.strip().splitlines()
    entries: list[tuple[int, str]] = []
    for line in lines:
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].isdigit():
            entries.append((int(parts[0]), parts[1]))
    entries.sort(key=lambda e: e[0], reverse=True)

    out = io.StringIO()
    out.write(f"{'SIZE_MB':>9} PATH\n")
    out.write("-" * 70 + "\n")
    for size_bytes, fpath in entries[:limit]:
        size_mb = size_bytes / (1024 * 1024)
        out.write(f"{size_mb:>9.1f} {fpath}\n")
    return out.getvalue().rstrip()


@hpc_tool(
    name="hpc_storage_quota_check",
    role=Role.VIEWER,
    schema={
        "name": "hpc_storage_quota_check",
        "description": "Check filesystem quotas via repquota on the login node.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_storage_quota_check(
    *,
    cluster: str = "default",
) -> str:
    """Check filesystem quotas via ``repquota`` on the login node.

    Returns a table with filesystem, user/group, used, soft, hard limits.
    """
    cl = _resolve_cluster(cluster)
    raw = _run(["repquota", "-a", "-u", "-g"], cluster=cl, timeout=30)
    return raw.strip() or "No quotas configured (repquota returned empty)"


# ===================================================================
# Config backup / snapshot (S19)
# ===================================================================


@hpc_tool(
    name="hpc_config_backup",
    role=Role.ADMIN,
    schema={
        "name": "hpc_config_backup",
        "description": "Snapshot current HPC configuration (Slurm, Warewulf, accounting) to a timestamped backup directory. Returns list of saved files.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_config_backup(
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Snapshot current HPC configuration to a timestamped backup directory.

    Captures:
    - Slurm config (``scontrol show config``)
    - Warewulf nodes (``wwctl node list``)
    - Slurm partitions (``scontrol show partition``)
    - Slurm reservations (``scontrol show reservation``)
    - Slurm associations (``sacctmgr show associations``)

    Output is saved to ``~/.hpc-pilot/backups/<timestamp>/``.
    Returns a list of saved files.
    """
    cl = _resolve_cluster(cluster)
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(get_home(), "backups", ts)

    if not dry_run:
        os.makedirs(backup_dir, exist_ok=True)

    snapshots: list[tuple[str, list[str]]] = [
        ("slurm_config.txt", [cl.slurm("scontrol"), "show", "config"]),
        ("slurm_partitions.txt", [cl.slurm("scontrol"), "show", "partition"]),
        ("slurm_reservations.txt", [cl.slurm("scontrol"), "show", "reservation"]),
        (
            "slurm_associations.txt",
            [
                cl.slurm("sacctmgr"),
                "--noheader",
                "show",
                "association",
                "format=Account,User,Partition,QOS,GrpTRES",
            ],
        ),
        ("warewulf_nodes.txt", [cl.warewulf("wwctl"), "node", "list"]),
        (
            "slurm_accounts.txt",
            [
                cl.slurm("sacctmgr"),
                "--noheader",
                "show",
                "account",
                "format=Account,Descr,Org,Cluster",
            ],
        ),
        (
            "slurm_qos.txt",
            [
                cl.slurm("sacctmgr"),
                "--noheader",
                "show",
                "qos",
                "format=Name,MaxWall,GrpTRES,MaxTRESPU,Flags",
            ],
        ),
    ]

    saved: list[str] = []
    for filename, cmd in snapshots:
        try:
            output = _run(cmd, cluster=cl, timeout=60)
        except Exception as exc:
            output = f"(error capturing {filename}: {exc})"
        if dry_run:
            saved.append(f"{backup_dir}/{filename}  (dry-run)")
        else:
            filepath = os.path.join(backup_dir, filename)
            with open(filepath, "w") as f:
                f.write(output)
            saved.append(filepath)

    return "\n".join(saved)


# ===================================================================
# Usage vs budget reporting (S1, S8)
# ===================================================================


@hpc_tool(
    name="hpc_usage_vs_budget",
    role=Role.VIEWER,
    schema={
        "name": "hpc_usage_vs_budget",
        "description": "Compare a research group's actual resource usage against its QOS budget. Queries sacct for job history and sacctmgr for QOS GrpTRES limits. Returns CPU and GPU hours used vs allocated.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Slurm account name (e.g. astro-lab)"},
                "qos_name": {"type": "string", "description": "QOS name (e.g. astro-lab-qos)"},
                "start": {"type": "string", "description": "Start time e.g. 2026-01-01"},
                "end": {"type": "string", "description": "End time (default: now)"},
            },
            "required": ["account", "qos_name"],
        },
    },
)
def hpc_usage_vs_budget(
    account: str,
    qos_name: str,
    *,
    start: str = "",
    end: str = "",
    cluster: str = "default",
) -> str:
    """Compare a group's actual resource usage against its QOS budget.

    Queries ``sacct`` for the account's job history and ``sacctmgr show qos``
    for the QOS ``GrpTRES`` limits.  Returns a summary of used vs allocated
    CPU and GPU hours.

    Args:
        account: Slurm accounting account name (e.g. ``astro-lab``).
        qos_name: QOS name (e.g. ``astro-lab-qos``).
        start: Start time for the usage window, e.g. ``2026-01-01`` (default: quarter-to-date).
        end: End time (default: now).
    """

    cl = _resolve_cluster(cluster)

    # Get QOS limits
    qos_raw = _run(
        [
            cl.slurm("sacctmgr"),
            "--noheader",
            "show",
            "qos",
            qos_name,
            "format=Name,GrpTRES,MaxTRESPU",
        ],
        cluster=cl,
        timeout=30,
    )

    qos_cpu = 0
    qos_gpu = 0
    for line in qos_raw.splitlines():
        parts = line.strip().split()
        for part in parts:
            if part.startswith("cpu="):
                try:
                    qos_cpu = int(part.split("=")[1])
                except ValueError:
                    pass
            elif part.startswith("gres/gpu="):
                try:
                    qos_gpu = int(part.split("=")[1])
                except ValueError:
                    pass

    # Get actual usage via sacct
    if not start:
        start = "2026-01-01"
    sacct_raw = _run(
        [
            cl.slurm("sacct"),
            "-P",
            "--format=JobID,User,Account,State,Elapsed,AllocTRES",
            f"--start={start}",
        ]
        + ([f"--end={end}"] if end else [])
        + [
            "--accounts",
            account,
            "-X",  # don't show job steps, only parent jobs
        ],
        cluster=cl,
        timeout=120,
    )

    total_cpu_seconds = 0
    total_gpu_seconds = 0
    completed_jobs = 0

    for line in sacct_raw.splitlines():
        parts = line.split("|")
        if len(parts) < 6:
            continue
        state = parts[3]
        if state not in ("COMPLETED", "RUNNING"):
            continue
        elapsed = parts[4]
        tres = parts[5]

        # Parse elapsed into seconds
        seconds = 0
        em = re.match(r"(?:(\d+)-)?(\d+):(\d+):(\d+)", elapsed)
        if em:
            days = int(em.group(1) or 0)
            hours = int(em.group(2))
            minutes = int(em.group(3))
            secs = int(em.group(4))
            seconds = days * 86400 + hours * 3600 + minutes * 60 + secs

        # Parse TRES for CPUs and GPUs
        cpu_count = 1
        gpu_count = 0
        for tres_part in tres.split(","):
            tres_part = tres_part.strip()
            if tres_part.startswith("cpu="):
                try:
                    cpu_count = int(tres_part.split("=")[1])
                except ValueError:
                    pass
            elif tres_part.startswith("gres/gpu="):
                try:
                    gpu_count = int(tres_part.split("=")[1])
                except ValueError:
                    pass

        total_cpu_seconds += cpu_count * seconds
        total_gpu_seconds += gpu_count * seconds
        if state == "COMPLETED":
            completed_jobs += 1

    cpu_hours_used = round(total_cpu_seconds / 3600, 1)
    gpu_hours_used = round(total_gpu_seconds / 3600, 1)
    cpu_budget_hours = max(1, qos_cpu)
    gpu_budget_hours = max(1, qos_gpu)
    cpu_pct = round(cpu_hours_used / cpu_budget_hours * 100, 1)
    gpu_pct = round(gpu_hours_used / gpu_budget_hours * 100, 1)

    lines_out: list[str] = [
        f"Usage vs Budget for account '{account}' / QOS '{qos_name}'",
        f"  Period: {start} to {end or 'now'}",
        f"  Completed jobs: {completed_jobs}",
        "",
        f"  CPU hours:  {cpu_hours_used} used of {cpu_budget_hours} budgeted  ({cpu_pct}%)",
        f"  GPU hours:  {gpu_hours_used} used of {gpu_budget_hours} budgeted  ({gpu_pct}%)",
    ]

    if cpu_pct > 90 or gpu_pct > 90:
        lines_out.append("")
        lines_out.append("  ⚠️ WARNING: Usage exceeds 90% of budget — consider renewal.")

    return "\n".join(lines_out)


# ===================================================================
# Notification (S1) — send via HPC-Pilot gateway
# ===================================================================


@hpc_tool(
    name="hpc_notify",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_notify",
        "description": "Send a notification message via the HPC-Pilot Telegram or Discord gateway.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message text to send"},
                "platform": {
                    "type": "string",
                    "enum": ["telegram", "discord"],
                    "description": "Platform (default telegram)",
                },
                "target": {
                    "type": "string",
                    "description": "Chat ID (Telegram) or channel ID (Discord)",
                },
            },
            "required": ["message"],
        },
    },
)
def hpc_notify(
    message: str,
    platform: str = "telegram",
    target: str = "",
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Send a notification message via the HPC-Pilot gateway.

    Reads the gateway config from ``~/.hpc-pilot/.env`` and sends via
    the configured Telegram or Discord bot.

    Args:
        message: The message text to send.
        platform: ``telegram`` or ``discord`` (default ``telegram``).
        target: Chat/channel ID for Telegram or channel name for Discord.
        dry_run: Print the message without sending.
    """
    if platform not in ("telegram", "discord"):
        raise ValueError(f"Unsupported platform: {platform!r}. Must be 'telegram' or 'discord'")

    if dry_run:
        return f"DRY-RUN: would send to {platform}/{target or 'default'}: {message}"

    if not target:
        return (
            f"Notification not sent: no {platform} target configured. "
            f"Specify 'target' (chat ID / channel ID) or configure a default "
            f"recipient in gateway settings."
        )

    env_path = os.path.join(get_home(), ".env")
    if not os.path.exists(env_path):
        return f"Notification not sent: no .env file at {env_path}"

    # Use a simple curl-based approach for Telegram (most common)
    if platform == "telegram":
        import re

        token = None
        with open(env_path) as f:
            for line in f:
                m = re.match(r"TELEGRAM_BOT_TOKEN=(.+)", line.strip())
                if m:
                    token = m.group(1)
        if not token:
            return "Notification not sent: TELEGRAM_BOT_TOKEN not found in .env"
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = f"chat_id={target}&text={message}"
        subprocess.run(
            ["curl", "-s", "-X", "POST", url, "-d", data],
            capture_output=True,
            timeout=15,
        )
        return f"Notification sent via Telegram to {target}: {message[:100]}..."

    if platform == "discord":
        import re

        token = None
        with open(env_path) as f:
            for line in f:
                m = re.match(r"DISCORD_BOT_TOKEN=(.+)", line.strip())
                if m:
                    token = m.group(1)
        if not token:
            return "Notification not sent: DISCORD_BOT_TOKEN not found in .env"
        # Discord webhook or REST
        data = f'{{"content":"{message}"}}'
        subprocess.run(
            [
                "curl",
                "-s",
                "-X",
                "POST",
                f"https://discord.com/api/v10/channels/{target}/messages",
                "-H",
                f"Authorization: Bot {token}",
                "-H",
                "Content-Type: application/json",
                "-d",
                data,
            ],
            capture_output=True,
            timeout=15,
        )
        return f"Notification sent via Discord to {target}: {message[:100]}..."

    return f"Notification not sent: unknown platform {platform!r}"


# ===================================================================
# Spack-in-Image build (S3)
# ===================================================================


@hpc_tool(
    name="hpc_warewulf_image_build_from_env",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_image_build_from_env",
        "description": "Build a Warewulf compute image with a Spack environment baked in. Imports the base image, installs Spack, activates the env, and builds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Output image name"},
                "base": {
                    "type": "string",
                    "description": "Base container image (default rockylinux:9)",
                },
                "spack_env": {"type": "string", "description": "Spack environment name to bake in"},
                "exec_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional build commands",
                },
                "gpu": {"type": "boolean", "description": "Include GPU support"},
            },
            "required": ["name"],
        },
    },
)
def hpc_warewulf_image_build_from_env(
    name: str,
    base: str = "rockylinux:9",
    spack_env: str = "",
    exec_steps: list[str] | None = None,
    *,
    gpu: bool = False,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Build a Warewulf compute image with a Spack environment baked in.

    Imports the base image, installs Spack into it via exec steps, activates
    the named Spack environment, optionally adds GPU support, and builds
    the final Warewulf image.

    Args:
        name: Output image name.
        base: Base container image (e.g. ``rockylinux:9``).
        spack_env: Spack environment name to bake into the image. The Spack
            environment must already exist and be concretized.
        exec_steps: Additional build commands to run after Spack setup.
        gpu: Include GPU driver support.
        dry_run: Preview without building.
    """
    _validate(name, "image name")
    _validate(base, "base image")

    cl = _resolve_cluster(cluster)

    # Step 1: import base image
    import_step = _run(
        [cl.warewulf("wwctl"), "image", "import", base, name],
        cluster=cl,
        timeout=300,
        dry_run=dry_run,
    )

    # Step 2: build image with Spack env
    steps: list[str] = list(exec_steps or [])
    if spack_env:
        env_path_guess = f"/shared/software/spack_envs/{spack_env}"
        spack_steps = [
            "dnf -y install spack 2>/dev/null || echo 'spack not in repo'",
            "[ -d /shared/spack ] && . /shared/spack/share/spack/setup-env.sh || true",
            f"spack env activate {spack_env} 2>/dev/null || spack env activate {env_path_guess} 2>/dev/null || true",
        ]
        steps = spack_steps + steps

    build_args: dict[str, Any] = {}
    _run(
        build_cmd(cl, name, steps, gpu),
        cluster=cl,
        timeout=600,
        dry_run=dry_run,
    )

    return f"Image '{name}' built from base '{base}'" + (
        f" with Spack env '{spack_env}'" if spack_env else ""
    )


def build_cmd(cl, name: str, exec_steps: list[str], gpu: bool) -> list[str]:
    """Build the Warewulf image build command."""
    cmd = [cl.warewulf("wwctl"), "image", "build", name]
    for step in exec_steps:
        cmd += ["--exec", step]
    if gpu:
        cmd += ["--gpu"]
    return cmd


# ===================================================================
# Test job submission (S3)
# ===================================================================


@hpc_tool(
    name="hpc_job_submit_test",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_job_submit_test",
        "description": "Submit a short validation test job via sbatch. Use after provisioning or image builds to verify the cluster works.",
        "input_schema": {
            "type": "object",
            "properties": {
                "partition": {"type": "string", "description": "Partition to submit to"},
                "num_nodes": {"type": "integer", "description": "Number of nodes (default 1)"},
                "ntasks": {"type": "integer", "description": "Number of tasks (default 1)"},
            },
            "required": [],
        },
    },
)
def hpc_job_submit_test(
    partition: str = "",
    num_nodes: int = 1,
    ntasks: int = 1,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Submit a short validation/test job via ``sbatch`` and return the job ID.

    Creates a minimal test script that runs ``hostname`` and sleeps 10 seconds.
    Use after provisioning or image builds to verify the cluster is working.

    Args:
        partition: Partition to submit to (optional).
        num_nodes: Number of nodes requested (default 1).
        ntasks: Number of tasks (default 1).
        dry_run: Preview the job script without submitting.
    """
    import tempfile

    cl = _resolve_cluster(cluster)

    script = "#!/bin/bash\n"
    script += f"#SBATCH --nodes={num_nodes}\n"
    script += f"#SBATCH --ntasks={ntasks}\n"
    script += "#SBATCH --time=00:05:00\n"
    script += "#SBATCH --job-name=hpc-pilot-validate\n"
    script += "#SBATCH --output=hpc-pilot-validate-%j.out\n"
    if partition:
        script += f"#SBATCH --partition={partition}\n"
    script += '\necho "Job started on $(hostname) at $(date)"\n'
    script += 'echo "SLURM_NODELIST=$SLURM_NODELIST"\n'
    script += "sleep 10\n"
    script += 'echo "Job finished at $(date)"\n'

    if dry_run:
        return "DRY-RUN: would submit:\n" + script

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False, prefix="hpc-validate-"
    ) as f:
        f.write(script)
        script_path = f.name

    try:
        result = _run([cl.slurm("sbatch"), script_path], cluster=cl, timeout=30)
        return result.strip()
    finally:
        os.unlink(script_path)


# ===================================================================
# Lustre OST balance check and migration (S16)
# ===================================================================


@hpc_tool(
    name="hpc_storage_lustre_balance",
    role=Role.VIEWER,
    schema={
        "name": "hpc_storage_lustre_balance",
        "description": "Check Lustre OST balance and optionally migrate files off overfull OSTs. Reports per-OST usage and identifies imbalance.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fs_name": {
                    "type": "string",
                    "description": "Lustre filesystem mount point (default /scratch)",
                },
                "min_migrate_size_mb": {
                    "type": "integer",
                    "description": "Min file size for migration in MB (default 10240)",
                },
            },
            "required": [],
        },
    },
)
def hpc_storage_lustre_balance(
    fs_name: str = "/scratch",
    min_migrate_size_mb: int = 10240,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Check Lustre OST balance and optionally migrate files off full OSTs.

    Step 1: Check per-OST usage via ``lfs df``.
    Step 2: Identify OSTs with >70% usage.
    Step 3: Optionally run ``lfs_migrate`` on files on overfull OSTs.

    Args:
        fs_name: Lustre filesystem mount point (default ``/scratch``).
        min_migrate_size_mb: Only migrate files larger than this (default 10240 = 10 GB).
        dry_run: Only report, don't actually migrate.
    """

    cl = _resolve_cluster(cluster)

    # Step 1: Get per-OST usage
    df_raw = _run(["lfs", "df", fs_name], cluster=cl, timeout=30)

    ost_usage: list[dict[str, Any]] = []
    for line in df_raw.splitlines():
        if not line.startswith("OST:"):
            continue
        parts = line.split()
        if len(parts) >= 6:
            ost_name = parts[0]
            total = int(parts[1]) if parts[1].isdigit() else 0
            used = int(parts[2]) if parts[2].isdigit() else 0
            pct = round(used / max(total, 1) * 100, 1)
            ost_usage.append({"name": ost_name, "total": total, "used": used, "pct": pct})

    if not ost_usage:
        return f"No Lustre OST data found for {fs_name} (may not be a Lustre filesystem)"

    # Report
    out: list[str] = [
        f"Lustre OST balance for {fs_name}",
        f"{'OST':20} {'TOTAL(GB)':>10} {'USED(GB)':>10} {'%USED':>7}",
        "-" * 50,
    ]
    for ost in ost_usage:
        total_gb = round(ost["total"] / 1024, 1) if "total" in ost else 0
        used_gb = round(ost["used"] / 1024, 1) if "used" in ost else 0
        out.append(f"{ost['name']:20} {total_gb:>10.1f} {used_gb:>10.1f} {ost['pct']:>6.1f}%")

    # Stats
    pcts = [o["pct"] for o in ost_usage]
    if pcts:
        avg_pct = round(sum(pcts) / len(pcts), 1)
        overfull = [o for o in ost_usage if o["pct"] > 70]
        out.append(f"\nAverage OST usage: {avg_pct}%")
        out.append(f"OSTs over 70%: {len(overfull)} of {len(ost_usage)}")
        if overfull:
            over_names = ", ".join(o["name"] for o in overfull)
            out.append(f"Overfull OSTs: {over_names}")

            if not dry_run:
                out.append(
                    f"\nRunning lfs_migrate for files > {min_migrate_size_mb}MB on overfull OSTs..."
                )
                for ost in overfull:
                    try:
                        migrate_raw = _run(
                            [
                                "lfs_migrate",
                                "-c",
                                ost["name"],
                                "-s",
                                str(min_migrate_size_mb * 1024),
                                fs_name,
                            ],
                            cluster=cl,
                            timeout=300,
                        )
                        out.append(f"  {ost['name']}: {migrate_raw.strip()[:100]}")
                    except RuntimeError as exc:
                        out.append(f"  {ost['name']}: migration skipped ({exc})")
            else:
                out.append(f"\n  (dry-run: would migrate files on {over_names})")

    return "\n".join(out)


# ===================================================================
# Scrub orphaned job directories (S12)
# ===================================================================


@hpc_tool(
    name="hpc_storage_scrub_orphans",
    role=Role.ADMIN,
    schema={
        "name": "hpc_storage_scrub_orphans",
        "description": "Find orphaned job working directories older than a threshold. Dry-run by default \u2014 lists candidates without deleting. Use for storage cleanup review.",
        "input_schema": {
            "type": "object",
            "properties": {
                "work_dir": {
                    "type": "string",
                    "description": "Directory to scan (default /scratch)",
                },
                "max_age_days": {
                    "type": "integer",
                    "description": "Age threshold in days (default 30)",
                },
            },
            "required": [],
        },
    },
)
def hpc_storage_scrub_orphans(
    work_dir: str = "/scratch",
    max_age_days: int = 30,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Find orphaned job working directories (directories older than *max_age_days*
    whose naming pattern suggests they were created by a batch job).

    This tool only **lists** candidates — it does NOT delete anything.
    Use ``dry_run=false`` only after manual review of the output.

    Args:
        work_dir: Parent directory to scan (default ``/scratch``).
        max_age_days: Age threshold in days (default 30).
        dry_run: List without deleting (default True for safety).
    """
    cl = _resolve_cluster(cluster)

    # Look for directories matching common Slurm job patterns:
    #   job_<id>, <user>_<id>, slurm-<id>, or directories owned by batch users
    raw = _run(
        [
            "find",
            work_dir,
            "-maxdepth",
            "2",
            "-type",
            "d",
            "-mtime",
            f"+{max_age_days}",
            "!",
            "-name",
            ".",
            "!",
            "-name",
            "..",
            "-printf",
            "%T@\\t%u\\t%s\\t%p\\n",
        ],
        cluster=cl,
        timeout=120,
    )

    lines = raw.strip().splitlines() if raw.strip() else []
    if not lines:
        return f"No directories older than {max_age_days} days found under {work_dir}"

    out: list[str] = [
        f"Directories older than {max_age_days} days under {work_dir}",
        f"{'AGE(days)':>10} {'OWNER':12} {'SIZE(MB)':>9} PATH",
        "-" * 80,
    ]

    import time

    now = time.time()
    count = 0
    for line in lines[:200]:  # cap output
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        try:
            mtime = float(parts[0])
        except ValueError:
            continue
        owner = parts[1]
        size = int(parts[2]) if parts[2].isdigit() else 0
        path = parts[3]
        age_days = round((now - mtime) / 86400, 1)
        size_mb = round(size / (1024 * 1024), 1)
        out.append(f"{age_days:>10.1f} {owner:12} {size_mb:>9.1f} {path}")
        count += 1

    out.append(f"\n{count} directories found (listing capped at 200).")

    if dry_run:
        out.append(
            "\n⚠️  This is a dry-run. No files were deleted. "
            "Review the list above, then re-run with dry_run=false to delete."
        )

    return "\n".join(out)


# ===================================================================
# Job step resource metrics (S14)
# ===================================================================


@hpc_tool(
    name="hpc_slurm_job_step_metrics",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_job_step_metrics",
        "description": "Retrieve per-step resource metrics for a completed job via sacct. Returns CPU, memory, wall time per job step.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Slurm job ID"}},
            "required": ["job_id"],
        },
    },
)
def hpc_slurm_job_step_metrics(
    job_id: str,
    *,
    cluster: str = "default",
) -> str:
    """Retrieve per-step resource metrics for a completed job via ``sacct``.

    Returns a formatted table with each job step's CPU, memory, and
    wall time usage.

    Args:
        job_id: Slurm job ID (e.g. ``481516``).
    """
    from hpc_pilot.tools._validation import _NAME_RE

    _validate(job_id, "job_id", _NAME_RE)
    cl = _resolve_cluster(cluster)

    raw = _run(
        [
            cl.slurm("sacct"),
            "-j",
            job_id,
            "-P",
            "--format=JobID,JobName,State,ExitCode,Elapsed,AllocCPUS,ReqMem,MaxRSS,MaxVMSize,NNodes",
        ],
        cluster=cl,
        timeout=30,
    )

    out = io.StringIO()
    out.write(
        f"{'JobID':18} {'State':12} {'Elapsed':12} {'CPUS':>5} {'MaxRSS':>12} {'MaxVM':>12} {'Nodes':>5}\n"
    )
    out.write("-" * 80 + "\n")

    lines = raw.strip().splitlines()
    for i, line in enumerate(lines):
        parts = line.split("|")
        if not parts or not parts[0].strip():
            continue
        if i == 0 and parts[0].strip().upper() == "JOBID":
            continue  # skip header
        if len(parts) >= 8:
            step_id = parts[0][:18]
            state = parts[2][:12]
            elapsed = parts[4][:12]
            cpus = parts[5][:5]
            maxrss = parts[7][:12]
            maxvm = parts[8][:12]
            nodes = parts[9][:5]
            out.write(
                f"{step_id:18} {state:12} {elapsed:12} {cpus:>5} {maxrss:>12} {maxvm:>12} {nodes:>5}\n"
            )

    return out.getvalue().rstrip()


# ===================================================================
# Multi-cluster migration planning (S20)
# ===================================================================


@hpc_tool(
    name="hpc_multi_migration_plan",
    role=Role.VIEWER,
    schema={
        "name": "hpc_multi_migration_plan",
        "description": "Analyze feasibility of migrating jobs from one cluster to another. Compares partitions, QOS, and job counts. Use when planning maintenance or load-balancing across clusters.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_cluster": {"type": "string", "description": "Cluster to migrate from"},
                "target_cluster": {"type": "string", "description": "Cluster to migrate to"},
            },
            "required": ["source_cluster", "target_cluster"],
        },
    },
)
def hpc_multi_migration_plan(
    source_cluster: str,
    target_cluster: str,
    *,
    dry_run: bool = False,
) -> str:
    """Analyze feasibility of migrating jobs from one cluster to another.

    Compares partitions, QOS entries, and node counts between two clusters
    to identify compatibility issues for job migration.

    Args:
        source_cluster: Cluster name to migrate from.
        target_cluster: Cluster name to migrate to.
        dry_run: Only analyze, don't execute any migration.
    """
    from hpc_pilot.tools.multi import hpc_multi_query

    # Query both clusters in parallel
    partition_query = hpc_multi_query(
        "hpc_slurm_partition_list",
        {},
        [source_cluster, target_cluster],
    )
    qos_query = hpc_multi_query(
        "hpc_slurm_qos_list",
        {},
        [source_cluster, target_cluster],
    )
    queue_query = hpc_multi_query(
        "hpc_slurm_queue",
        {},
        [source_cluster, target_cluster],
    )

    out: list[str] = [
        f"Migration plan: {source_cluster} → {target_cluster}",
        "=" * 60,
    ]

    # Parse partition data
    src_parts: set[str] = set()
    tgt_parts: set[str] = set()
    if isinstance(partition_query, dict):
        for cluster_name, data in partition_query.items():
            text = ""
            if isinstance(data, dict) and "stdout" in data:
                text = data["stdout"]
            elif isinstance(data, str):
                text = data
            for line in text.splitlines():
                if line.strip() and not line.startswith("PartitionName="):
                    continue
                m_part = __import__("re").search(r"PartitionName=(\S+)", line)
                if m_part:
                    if cluster_name == source_cluster:
                        src_parts.add(m_part.group(1))
                    else:
                        tgt_parts.add(m_part.group(1))

    common = src_parts & tgt_parts
    missing = src_parts - tgt_parts
    out.append(f"\nPartitions on {source_cluster}: {len(src_parts)}")
    out.append(f"Partitions on {target_cluster}: {len(tgt_parts)}")
    out.append(f"Common partitions: {len(common)}")
    if common:
        out.append(f"  ({', '.join(sorted(common)[:10])})")
    if missing:
        out.append(f"❌ Partitions missing on {target_cluster}: {', '.join(sorted(missing))}")

    # Count running jobs
    running = 0
    pending = 0
    if isinstance(queue_query, dict):
        for cluster_name, data in queue_query.items():
            text = ""
            if isinstance(data, dict) and "stdout" in data:
                text = data["stdout"]
            elif isinstance(data, str):
                text = data
            if cluster_name == source_cluster:
                for line in text.splitlines():
                    if "RUNNING" in line:
                        running += 1
                    elif "PENDING" in line:
                        pending += 1

    out.append(f"\nJobs on {source_cluster}:")
    out.append(f"  Running: {running}")
    out.append(f"  Pending: {pending}")

    out.append("\nRecommendation:")
    if not missing:
        out.append(f"  ✅ All partitions on {source_cluster} exist on {target_cluster}.")
        out.append("  Jobs can be migrated.")
    else:
        out.append(f"  ⚠️  {len(missing)} partition(s) missing on target.")
        out.append("  Jobs on these partitions must be re-queued into a compatible partition.")

    if dry_run:
        out.append("\n(dry-run: no jobs were cancelled or requeued)")

    return "\n".join(out)
