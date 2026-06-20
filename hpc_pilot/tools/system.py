"""HPC system administration tools — remaining utilities not yet split into domain modules."""

from __future__ import annotations

import io
import os
import re
import time
from typing import Any

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate


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
        lines_out.append("  WARNING: Usage exceeds 90% of budget — consider renewal.")

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

    if platform == "telegram":
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
        _run(["curl", "-s", "-X", "POST", url, "-d", data], timeout=15)
        return f"Notification sent via Telegram to {target}: {message[:100]}..."

    if platform == "discord":
        token = None
        with open(env_path) as f:
            for line in f:
                m = re.match(r"DISCORD_BOT_TOKEN=(.+)", line.strip())
                if m:
                    token = m.group(1)
        if not token:
            return "Notification not sent: DISCORD_BOT_TOKEN not found in .env"
        data = f'{{"content":"{message}"}}'
        _run(
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
            timeout=15,
        )
        return f"Notification sent via Discord to {target}: {message[:100]}..."

    return f"Notification not sent: unknown platform {platform!r}"


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
                m_part = re.search(r"PartitionName=(\S+)", line)
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
        out.append(f"Partitions missing on {target_cluster}: {', '.join(sorted(missing))}")

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
        out.append(f"  All partitions on {source_cluster} exist on {target_cluster}.")
        out.append("  Jobs can be migrated.")
    else:
        out.append(f"  {len(missing)} partition(s) missing on target.")
        out.append("  Jobs on these partitions must be re-queued into a compatible partition.")

    if dry_run:
        out.append("\n(dry-run: no jobs were cancelled or requeued)")

    return "\n".join(out)
