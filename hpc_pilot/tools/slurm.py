"""Slurm tools and output parsers."""

from __future__ import annotations

import os
import re
from typing import Any

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _NAME_RE, _USER_RE, _validate

# ---------------------------------------------------------------------------
# Validation patterns
# ---------------------------------------------------------------------------

_JOB_ID_RE = re.compile(r"^[0-9]+(_[0-9]+)?$")
_SAFE_TIME_RE = re.compile(r"^[0-9T:.+Znow -][0-9T:.+Znow+-]*$")
_DURATION_RE = re.compile(r"^[0-9][0-9dhm:]*$")
_FLAGS_RE = re.compile(r"^[a-zA-Z,]+$")
_NODES_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\[\],.-]*$")


# ---------------------------------------------------------------------------
# Slurm tools — query (viewer)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_slurm_node_status",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_node_status",
        "description": "Show detailed Slurm node status (CPU, memory, state, running jobs). Leave node empty to show all nodes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Node name. Empty string = show all nodes.",
                }
            },
            "required": [],
        },
    },
)
def hpc_slurm_node_status(node: str = "", *, cluster: str = "default") -> str:
    """Return scontrol node info for *node*, or all nodes when *node* is empty."""
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("scontrol"), "show", "node"]
    if node:
        cmd.append(node)
    return _run(cmd, cluster=cl, timeout=90)


def _dispatch_hpc_slurm_queue(args: dict[str, Any], tools: Any) -> str:
    """Custom dispatch for hpc_slurm_queue — builds filters dict from individual args."""
    filters = {k: v for k, v in args.items() if k in ("user", "partition", "state") and v} or None
    result = tools.hpc_slurm_queue(filters, cluster=args.get("cluster", "default"))
    if isinstance(result, dict):
        return __import__("json").dumps(result, indent=2, default=str)
    return str(result)


@hpc_tool(
    name="hpc_slurm_queue",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_queue",
        "description": "Show the Slurm job queue, optionally filtered by user, partition or state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Filter by username"},
                "partition": {"type": "string", "description": "Filter by partition name"},
                "state": {
                    "type": "string",
                    "description": "Filter by job state, e.g. RUNNING, PENDING",
                },
            },
            "required": [],
        },
    },
    handler=_dispatch_hpc_slurm_queue,
)
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


@hpc_tool(
    name="hpc_slurm_job_status",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_job_status",
        "description": "Show detailed status for a single Slurm job (scontrol show job).",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Slurm job ID (e.g. '12345')"}
            },
            "required": ["job_id"],
        },
    },
)
def hpc_slurm_job_status(job_id: str, *, cluster: str = "default") -> str:
    """Return scontrol detail for a single job."""
    _validate(job_id, "job_id", _JOB_ID_RE)
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "show", "job", job_id, "-o"], cluster=cl)


@hpc_tool(
    name="hpc_slurm_reservation_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_reservation_list",
        "description": "List all active Slurm reservations.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_reservation_list(*, cluster: str = "default") -> str:
    """List all Slurm reservations."""
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "show", "reservation"], cluster=cl)


@hpc_tool(
    name="hpc_slurm_partition_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_partition_list",
        "description": "List all Slurm partitions with their configuration.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_partition_list(*, cluster: str = "default") -> str:
    """List all Slurm partitions with configuration."""
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "show", "partition"], cluster=cl)


@hpc_tool(
    name="hpc_slurm_account_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_account_list",
        "description": "List Slurm accounting accounts.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_account_list(*, cluster: str = "default") -> str:
    """List Slurm accounting accounts."""
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.slurm("sacctmgr"), "--noheader", "show", "account", "format=Account,Descr,Org,Cluster"],
        cluster=cl,
    )


@hpc_tool(
    name="hpc_slurm_association_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_association_list",
        "description": "List Slurm user-account associations, optionally filtered.",
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string"}, "user": {"type": "string"}},
            "required": [],
        },
    },
)
def hpc_slurm_association_list(
    account: str = "",
    user: str = "",
    *,
    cluster: str = "default",
) -> str:
    """List Slurm accounting associations, optionally filtered by account or user."""
    _validate(account, "account", _USER_RE)
    _validate(user, "user", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = [
        cl.slurm("sacctmgr"),
        "--noheader",
        "show",
        "association",
        "format=Account,User,Partition,QOS,GrpTRES",
    ]
    if account:
        cmd.append(f"Account={account}")
    if user:
        cmd.append(f"User={user}")
    return _run(cmd, cluster=cl)


@hpc_tool(
    name="hpc_slurm_qos_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_qos_list",
        "description": "List all Slurm QOS entries with their limits.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_qos_list(*, cluster: str = "default") -> str:
    """List all Slurm QOS entries."""
    cl = _resolve_cluster(cluster)
    return _run(
        [
            cl.slurm("sacctmgr"),
            "--noheader",
            "show",
            "qos",
            "format=Name,MaxWall,MaxTRESPU,GrpTRES,Flags",
        ],
        cluster=cl,
    )


@hpc_tool(
    name="hpc_slurm_fairshare",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_fairshare",
        "description": "Show Slurm fairshare usage (sshare -Pl).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_fairshare(*, cluster: str = "default") -> str:
    """Show fairshare usage (sshare -Pl)."""
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("sshare"), "-Pl"], cluster=cl)


@hpc_tool(
    name="hpc_slurm_accounting",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_accounting",
        "description": "Query Slurm job accounting history (sacct). Filter by user, account, time range, or job state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "account": {"type": "string"},
                "start": {"type": "string", "description": "Start time, e.g. '2026-06-01'"},
                "end": {"type": "string", "description": "End time"},
                "state": {"type": "string", "description": "State filter, e.g. 'FAILED,TIMEOUT'"},
            },
            "required": [],
        },
    },
)
def hpc_slurm_accounting(
    user: str = "",
    account: str = "",
    start: str = "",
    end: str = "",
    state: str = "",
    *,
    cluster: str = "default",
) -> str:
    """Query Slurm job accounting history (sacct -P)."""
    _validate(user, "user", _USER_RE)
    _validate(account, "account", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = [
        cl.slurm("sacct"),
        "-P",
        "--format=JobID,JobName,User,Account,State,Elapsed,AllocTRES,Submit,Start,End",
    ]
    if user:
        cmd += ["--user", user]
    if account:
        cmd += ["--account", account]
    if start:
        if not re.match(r"^[0-9T:.+Znow -][0-9T:.+Znow+-]*$", start):
            raise ValueError(f"Invalid start time: {start!r}")
        cmd += ["--starttime", start]
    if end:
        if not re.match(r"^[0-9T:.+Znow -][0-9T:.+Znow+-]*$", end):
            raise ValueError(f"Invalid end time: {end!r}")
        cmd += ["--endtime", end]
    if state:
        if not re.match(r"^[a-zA-Z,]+$", state):
            raise ValueError(f"Invalid state filter: {state!r}")
        cmd += ["--state", state]
    return _run(cmd, cluster=cl, timeout=120)


@hpc_tool(
    name="hpc_slurm_usage_report",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_usage_report",
        "description": "Generate a Slurm usage report (sreport). type: cluster, account, or user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "report_type": {
                    "type": "string",
                    "enum": ["cluster", "account", "user"],
                    "description": "Type of usage report",
                },
                "start": {"type": "string", "description": "Start date, e.g. '2026-06-01'"},
                "end": {"type": "string", "description": "End date"},
            },
            "required": [],
        },
    },
)
def hpc_slurm_usage_report(
    report_type: str = "cluster",
    start: str = "",
    end: str = "",
    *,
    cluster: str = "default",
) -> str:
    """Generate a Slurm usage report via sreport."""
    allowed = {"cluster", "account", "user"}
    if report_type not in allowed:
        raise ValueError(f"Invalid report_type: {report_type!r}. Must be one of {sorted(allowed)}")
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("sreport"), "-P", f"{report_type}", "utilization"]
    if start:
        if not re.match(r"^[0-9T:.+Znow -][0-9T:.+Znow+-]*$", start):
            raise ValueError(f"Invalid start time: {start!r}")
        cmd += ["start=" + start]
    if end:
        if not re.match(r"^[0-9T:.+Znow -][0-9T:.+Znow+-]*$", end):
            raise ValueError(f"Invalid end time: {end!r}")
        cmd += ["end=" + end]
    return _run(cmd, cluster=cl, timeout=120)


@hpc_tool(
    name="hpc_slurm_sdiag",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_sdiag",
        "description": "Show Slurm scheduler diagnostics (sdiag): cycle times, backfill depth, DBD state. Use to diagnose scheduling slowness.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_sdiag(*, cluster: str = "default") -> dict[str, Any]:
    """Return parsed Slurm scheduler diagnostics (sdiag)."""
    from hpc_pilot.tools.slurm_parsers import parse_sdiag

    cl = _resolve_cluster(cluster)
    output = _run([cl.slurm("sdiag")], cluster=cl, timeout=30)
    return parse_sdiag(output)


@hpc_tool(
    name="hpc_slurm_config_show",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_config_show",
        "description": "Show the active Slurm controller configuration (scontrol show config).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_config_show(*, cluster: str = "default") -> str:
    """Show the active Slurm configuration (scontrol show config)."""
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "show", "config"], cluster=cl, timeout=30)


# ---------------------------------------------------------------------------
# Slurm tools — mutation (operator)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_slurm_node_state",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_slurm_node_state",
        "description": "Change a Slurm node's state. drain = prevent new jobs; undrain/resume = allow jobs again; down = mark failed. Always query current state before changing it. Use dry_run=true to preview without executing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "target": {"type": "string", "enum": ["drain", "undrain", "resume", "down"]},
                "reason": {"type": "string", "description": "Reason for the state change"},
            },
            "required": ["node", "target"],
        },
    },
)
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


@hpc_tool(
    name="hpc_slurm_job_hold",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_slurm_job_hold",
        "description": "Put a pending Slurm job on hold so it does not start.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Slurm job ID"}},
            "required": ["job_id"],
        },
    },
)
def hpc_slurm_job_hold(
    job_id: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Put a pending job on hold (scontrol hold)."""
    _validate(job_id, "job_id", _JOB_ID_RE)
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "hold", job_id], cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_slurm_job_release",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_slurm_job_release",
        "description": "Release a held Slurm job so it may be scheduled.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Slurm job ID"}},
            "required": ["job_id"],
        },
    },
)
def hpc_slurm_job_release(
    job_id: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Release a held job (scontrol release)."""
    _validate(job_id, "job_id", _JOB_ID_RE)
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "release", job_id], cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_slurm_job_requeue",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_slurm_job_requeue",
        "description": "Requeue a running or failed Slurm job.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Slurm job ID"}},
            "required": ["job_id"],
        },
    },
)
def hpc_slurm_job_requeue(
    job_id: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Requeue a running or completed job (scontrol requeue)."""
    _validate(job_id, "job_id", _JOB_ID_RE)
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "requeue", job_id], cluster=cl, dry_run=dry_run)


def _extract_job_owner(scontrol_output: str) -> str:
    """Extract the job owner username from ``scontrol show job`` output."""
    m = re.search(r"\bUserId=([^( \t]+)", scontrol_output)
    if m:
        return m.group(1).split("(")[0]
    return ""


def _actor_username(actor: str) -> str:
    """Best-effort extraction of a UNIX username from an actor string.

    Gateway actor strings look like ``telegram:chat=123:user=alice``.
    Plain actor strings may be the UNIX username directly.
    """
    if re.match(r"^[a-z_][a-z0-9_.-]*$", actor):
        return actor
    m = re.search(r"user=([a-zA-Z0-9_.-]+)", actor)
    if m:
        return m.group(1)
    return os.environ.get("USER", "")


@hpc_tool(
    name="hpc_slurm_job_cancel",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_slurm_job_cancel",
        "description": "Cancel a Slurm job. Operators may only cancel jobs they own; admins may cancel any job.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Slurm job ID"}},
            "required": ["job_id"],
        },
    },
)
def hpc_slurm_job_cancel(
    job_id: str,
    *,
    actor: str = "cli",
    role: Any = None,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Cancel a job. Operators may only cancel their own jobs; admins may cancel any."""
    from hpc_pilot.rbac import Role

    _validate(job_id, "job_id", _JOB_ID_RE)
    cl = _resolve_cluster(cluster)

    if role is None or (isinstance(role, Role) and role < Role.ADMIN):
        owner_out = _run([cl.slurm("scontrol"), "show", "job", job_id, "-o"], cluster=cl)
        owner = _extract_job_owner(owner_out)
        actor_user = _actor_username(actor)
        if owner and actor_user and owner != actor_user:
            raise PermissionError(
                f"job {job_id} is owned by '{owner}'; actor '{actor_user}' may not cancel it"
            )

    return _run([cl.slurm("scancel"), job_id], cluster=cl, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Slurm tools — mutation (admin)
# ---------------------------------------------------------------------------


def _qos_tres_flag(value: str | None, flag: str) -> list[str]:
    """Build TRES-related sacctmgr flags like GrpTRES= or MaxTRESPU=.

    Expected format: ``"cpu=500000,gres/gpu=100000"``
    """
    if not value:
        return []
    # Validate: comma-separated key=value pairs
    for pair in value.split(","):
        pair = pair.strip()
        if not pair or "=" not in pair:
            raise ValueError(f"Invalid TRES value: {pair!r} in {value!r}")
    return [f"{flag}={value}"]


@hpc_tool(
    name="hpc_slurm_qos_modify",
    role=Role.ADMIN,
    schema={
        "name": "hpc_slurm_qos_modify",
        "description": "Modify a Slurm QOS (Quality of Service) setting. Supports MaxWall, GrpTRES (group-level TRES limits), and MaxTRESPU (per-user TRES limits). Use dry_run=true first to preview the sacctmgr command before applying.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "QOS name"},
                "max_wall_min": {
                    "type": "integer",
                    "description": "Maximum wall-clock time in minutes",
                },
                "grp_tres": {
                    "type": "string",
                    "description": "Group TRES limits, e.g. 'cpu=500000,gres/gpu=100000'",
                },
                "max_tres_per_user": {
                    "type": "string",
                    "description": "Per-user TRES limits, e.g. 'cpu=1000,gres/gpu=50'",
                },
            },
            "required": ["name"],
        },
    },
)
def hpc_slurm_qos_modify(
    name: str,
    max_wall_min: int | None = None,
    grp_tres: str | None = None,
    max_tres_per_user: str | None = None,
    dry_run: bool = False,
    *,
    cluster: str = "default",
) -> str:
    """Modify a Slurm QOS entry.

    Args:
        name: QOS name.
        max_wall_min: Maximum wall-clock time in minutes.
        grp_tres: Group TRES limits, e.g. ``"cpu=500000,gres/gpu=100000"``.
        max_tres_per_user: Per-user TRES limits, e.g. ``"cpu=1000,gres/gpu=50"``.
        dry_run: Preview without executing.
    """
    _validate(name, "QOS name", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("sacctmgr"), "--immediate", "modify", "qos", name, "set"]
    if max_wall_min is not None:
        cmd.append(f"MaxWall={max_wall_min}")
    cmd += _qos_tres_flag(grp_tres, "GrpTRES")
    cmd += _qos_tres_flag(max_tres_per_user, "MaxTRESPU")
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_slurm_qos_create",
    role=Role.ADMIN,
    schema={
        "name": "hpc_slurm_qos_create",
        "description": "Create a new Slurm QOS entry (sacctmgr add qos). Supports GrpTRES for group allocation limits. Requires admin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "max_wall_min": {"type": "integer", "description": "Max wall time in minutes"},
                "grp_tres": {
                    "type": "string",
                    "description": "Group TRES limits, e.g. 'cpu=500000,gres/gpu=100000'",
                },
                "max_tres_per_user": {
                    "type": "string",
                    "description": "Per-user TRES limits, e.g. 'cpu=1000,gres/gpu=50'",
                },
            },
            "required": ["name"],
        },
    },
)
def hpc_slurm_qos_create(
    name: str,
    max_wall_min: int | None = None,
    grp_tres: str | None = None,
    max_tres_per_user: str | None = None,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Create a new Slurm QOS entry (sacctmgr add qos).

    Args:
        name: QOS name.
        max_wall_min: Maximum wall-clock time in minutes.
        grp_tres: Group TRES limits, e.g. ``"cpu=500000,gres/gpu=100000"``.
        max_tres_per_user: Per-user TRES limits, e.g. ``"cpu=1000,gres/gpu=50"``.
        dry_run: Preview without executing.
    """
    _validate(name, "QOS name", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("sacctmgr"), "--immediate", "add", "qos", name]
    if max_wall_min is not None:
        cmd += [f"MaxWall={max_wall_min}"]
    cmd += _qos_tres_flag(grp_tres, "GrpTRES")
    cmd += _qos_tres_flag(max_tres_per_user, "MaxTRESPU")
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_slurm_reservation_create",
    role=Role.ADMIN,
    schema={
        "name": "hpc_slurm_reservation_create",
        "description": "Create a Slurm reservation for scheduled maintenance or events. Use dry_run=true to preview the scontrol command first.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Reservation name"},
                "nodes": {"type": "string", "description": "Node list or range, e.g. node[01-04]"},
                "start": {"type": "string", "description": "Start time, e.g. 'now'"},
                "duration": {"type": "string", "description": "Duration, e.g. '4:00:00'"},
                "users": {"type": "string", "description": "Comma-separated allowed users"},
                "accounts": {"type": "string", "description": "Comma-separated allowed accounts"},
                "flags": {"type": "string", "description": "Flags, e.g. 'MAINT,IGNORE_JOBS'"},
            },
            "required": ["name", "nodes", "start", "duration"],
        },
    },
)
def hpc_slurm_reservation_create(
    name: str,
    nodes: str,
    start: str,
    duration: str,
    users: str = "",
    accounts: str = "",
    flags: str = "",
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Create a Slurm reservation (scontrol create reservation)."""
    _validate(name, "reservation name", _NAME_RE)
    _validate(nodes, "nodes", _NODES_RE)
    if not start:
        raise ValueError("start time is required")
    if not re.match(r"^[0-9T:.+Znow -][0-9T:.+Znow+-]*$|^now$", start):
        raise ValueError(f"Invalid start time: {start!r}")
    if not duration or not _DURATION_RE.match(duration):
        raise ValueError(f"Invalid duration: {duration!r}")
    cl = _resolve_cluster(cluster)
    cmd = [
        cl.slurm("scontrol"),
        "create",
        "reservation",
        f"reservationname={name}",
        f"nodes={nodes}",
        f"starttime={start}",
        f"duration={duration}",
    ]
    if users:
        _validate(users.replace(",", ""), "users", _USER_RE)
        cmd.append(f"users={users}")
    if accounts:
        _validate(accounts.replace(",", ""), "accounts", _USER_RE)
        cmd.append(f"accounts={accounts}")
    if flags:
        if not _FLAGS_RE.match(flags):
            raise ValueError(f"Invalid flags: {flags!r}")
        cmd.append(f"flags={flags}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_slurm_reservation_update",
    role=Role.ADMIN,
    schema={
        "name": "hpc_slurm_reservation_update",
        "description": "Update an existing Slurm reservation. Use dry_run=true to preview.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Reservation name"},
                "nodes": {"type": "string"},
                "start": {"type": "string"},
                "duration": {"type": "string"},
                "users": {"type": "string"},
                "flags": {"type": "string"},
            },
            "required": ["name"],
        },
    },
)
def hpc_slurm_reservation_update(
    name: str,
    nodes: str = "",
    start: str = "",
    duration: str = "",
    users: str = "",
    flags: str = "",
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Update an existing Slurm reservation (scontrol update reservation)."""
    _validate(name, "reservation name", _NAME_RE)
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("scontrol"), "update", f"reservationname={name}"]
    if nodes:
        _validate(nodes, "nodes", _NODES_RE)
        cmd.append(f"nodes={nodes}")
    if start:
        if not re.match(r"^[0-9T:.+Znow -][0-9T:.+Znow+-]*$|^now$", start):
            raise ValueError(f"Invalid start time: {start!r}")
        cmd.append(f"starttime={start}")
    if duration:
        if not _DURATION_RE.match(duration):
            raise ValueError(f"Invalid duration: {duration!r}")
        cmd.append(f"duration={duration}")
    if users:
        _validate(users.replace(",", ""), "users", _USER_RE)
        cmd.append(f"users={users}")
    if flags:
        if not _FLAGS_RE.match(flags):
            raise ValueError(f"Invalid flags: {flags!r}")
        cmd.append(f"flags={flags}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_slurm_reservation_delete",
    role=Role.ADMIN,
    schema={
        "name": "hpc_slurm_reservation_delete",
        "description": "Delete a Slurm reservation. Use dry_run=true to preview.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Reservation name"}},
            "required": ["name"],
        },
    },
)
def hpc_slurm_reservation_delete(
    name: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Delete a Slurm reservation (scontrol delete reservation)."""
    _validate(name, "reservation name", _NAME_RE)
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.slurm("scontrol"), "delete", f"reservationname={name}"],
        cluster=cl,
        dry_run=dry_run,
    )


@hpc_tool(
    name="hpc_slurm_partition_update",
    role=Role.ADMIN,
    schema={
        "name": "hpc_slurm_partition_update",
        "description": "Update a Slurm partition setting (state, max time). Always use dry_run=true first \u2014 this is cluster-wide.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Partition name"},
                "state": {
                    "type": "string",
                    "enum": ["up", "down", "drain", "inactive"],
                    "description": "New partition state",
                },
                "max_time": {"type": "string", "description": "Max wall time, e.g. '7-00:00:00'"},
            },
            "required": ["name"],
        },
    },
)
def hpc_slurm_partition_update(
    name: str,
    state: str = "",
    max_time: str = "",
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Update a Slurm partition setting (scontrol update partition).

    Always use dry_run=True first — partition changes are cluster-wide.
    """
    _validate(name, "partition name", _NAME_RE)
    allowed_states = {"up", "down", "drain", "inactive"}
    if state and state.lower() not in allowed_states:
        raise ValueError(f"Invalid state: {state!r}. Must be one of {sorted(allowed_states)}")
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("scontrol"), "update", f"partitionname={name}"]
    if state:
        cmd.append(f"state={state.upper()}")
    if max_time:
        if not re.match(r"^[0-9][0-9:dhm]*$|^UNLIMITED$|^INFINITE$", max_time, re.IGNORECASE):
            raise ValueError(f"Invalid max_time: {max_time!r}")
        cmd.append(f"maxtime={max_time}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Slurm tools — mutation (superadmin)
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_slurm_account_create",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_slurm_account_create",
        "description": "Create a Slurm accounting account (sacctmgr add account). Superadmin only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Account name"},
                "description": {"type": "string"},
                "organization": {"type": "string"},
            },
            "required": ["name"],
        },
    },
)
def hpc_slurm_account_create(
    name: str,
    description: str = "",
    organization: str = "",
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Create a Slurm accounting account (sacctmgr add account)."""
    _validate(name, "account name", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = [cl.slurm("sacctmgr"), "--immediate", "add", "account", name]
    if description:
        cmd.append(f"Description={description}")
    if organization:
        cmd.append(f"Organization={organization}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_slurm_association_create",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_slurm_association_create",
        "description": "Associate a user with a Slurm account (sacctmgr add user). Superadmin.",
        "input_schema": {
            "type": "object",
            "properties": {"user": {"type": "string"}, "account": {"type": "string"}},
            "required": ["user", "account"],
        },
    },
)
def hpc_slurm_association_create(
    user: str,
    account: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Add a user-account association (sacctmgr add user account=X)."""
    _validate(user, "user", _USER_RE)
    _validate(account, "account", _USER_RE)
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.slurm("sacctmgr"), "--immediate", "add", "user", user, f"account={account}"],
        cluster=cl,
        dry_run=dry_run,
    )


@hpc_tool(
    name="hpc_slurm_reconfigure",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_slurm_reconfigure",
        "description": "Signal the Slurm controller to reload its configuration (scontrol reconfigure). Requires superadmin. Use dry_run=true to preview.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_slurm_reconfigure(
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Signal the Slurm controller to reload its configuration (scontrol reconfigure)."""
    cl = _resolve_cluster(cluster)
    return _run([cl.slurm("scontrol"), "reconfigure"], cluster=cl, dry_run=dry_run)


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


def parse_node_state_histogram(nodes: dict[str, Any]) -> dict[str, int]:
    """Build a state-count histogram from parsed scontrol node output."""
    histogram: dict[str, int] = {}
    for info in nodes.values():
        raw = info.get("NodeState", "UNKNOWN")
        # Strip trailing modifiers like +CLOUD, *
        state = re.sub(r"[+*].*$", "", raw).strip().upper()
        histogram[state] = histogram.get(state, 0) + 1
    return histogram
