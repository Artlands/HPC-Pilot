"""Centralized tool invocation: RBAC check → audit → dispatch → result string."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from hpc_pilot.audit import AuditEvent, audit_tool, log_audit
from hpc_pilot.rbac import Role, check_permission


def invoke(
    name: str,
    args: dict[str, Any],
    *,
    role: Role,
    actor: str,
    dry_run: bool = False,
) -> str:
    """RBAC-check, audit-log, and execute one HPC tool call.

    Permission denials are audited with returncode=126 before re-raising.
    """
    try:
        check_permission(name, role)
    except PermissionError as exc:
        log_audit(AuditEvent(
            tool=name,
            actor=actor,
            role=role.value,
            args=args,
            dry_run=dry_run,
            ts=time.time(),
            returncode=126,
            error=f"permission_denied: {exc}",
        ))
        raise

    from hpc_pilot import tools

    with audit_tool(name, actor, role.value, args, dry_run=dry_run):
        if name in ("hpc_skill_describe", "hpc_skill_run"):
            result = _dispatch_skill(name, args, role, actor)
        elif name == "hpc_slurm_job_cancel":
            result = _dispatch_job_cancel(args, tools, role, actor)
        else:
            result = _dispatch(name, args, tools)
    return result or "(no output)"


# ---------------------------------------------------------------------------
# Dispatch registry — maps tool name → callable(args, tools) → str
# ---------------------------------------------------------------------------

def _mk(
    fn_name: str, *positional_keys: str, **kwarg_keys: str
) -> Callable[[dict[str, Any], Any], str]:
    """Build a dispatch handler for tools with simple positional + keyword args."""
    def _handler(args: dict[str, Any], tools: Any) -> str:
        pos = [args[k] for k in positional_keys]
        kw = {dest: args[src] for dest, src in kwarg_keys.items() if src in args}
        cluster = args.get("cluster", "default")
        result = getattr(tools, fn_name)(*pos, cluster=cluster, **kw)
        if isinstance(result, dict):
            return json.dumps(result, indent=2, default=str)
        return str(result)
    return _handler


def _cl(args: dict[str, Any]) -> str:
    return args.get("cluster", "default")


def _dr(args: dict[str, Any], default: bool = False) -> bool:
    return bool(args.get("dry_run", default))


_DISPATCH: dict[str, Callable[[dict[str, Any], Any], str]] = {
    # ---- Slurm query ----
    "hpc_slurm_node_status": lambda args, t: t.hpc_slurm_node_status(
        args.get("node", ""), cluster=_cl(args)
    ),
    "hpc_slurm_queue": lambda args, t: t.hpc_slurm_queue(
        {k: v for k, v in args.items() if k in ("user", "partition", "state") and v} or None,
        cluster=_cl(args),
    ),
    "hpc_slurm_job_status": lambda args, t: t.hpc_slurm_job_status(
        args["job_id"], cluster=_cl(args)
    ),
    "hpc_slurm_reservation_list": lambda args, t: t.hpc_slurm_reservation_list(
        cluster=_cl(args)
    ),
    "hpc_slurm_partition_list": lambda args, t: t.hpc_slurm_partition_list(
        cluster=_cl(args)
    ),
    "hpc_slurm_account_list": lambda args, t: t.hpc_slurm_account_list(
        cluster=_cl(args)
    ),
    "hpc_slurm_association_list": lambda args, t: t.hpc_slurm_association_list(
        args.get("account", ""),
        args.get("user", ""),
        cluster=_cl(args),
    ),
    "hpc_slurm_qos_list": lambda args, t: t.hpc_slurm_qos_list(cluster=_cl(args)),
    "hpc_slurm_fairshare": lambda args, t: t.hpc_slurm_fairshare(cluster=_cl(args)),
    "hpc_slurm_accounting": lambda args, t: t.hpc_slurm_accounting(
        args.get("user", ""),
        args.get("account", ""),
        args.get("start", ""),
        args.get("end", ""),
        args.get("state", ""),
        cluster=_cl(args),
    ),
    "hpc_slurm_usage_report": lambda args, t: t.hpc_slurm_usage_report(
        args.get("report_type", "cluster"),
        args.get("start", ""),
        args.get("end", ""),
        cluster=_cl(args),
    ),
    "hpc_slurm_sdiag": lambda args, t: json.dumps(
        t.hpc_slurm_sdiag(cluster=_cl(args)), indent=2, default=str
    ),
    "hpc_slurm_config_show": lambda args, t: t.hpc_slurm_config_show(cluster=_cl(args)),
    # ---- Slurm mutation (operator) ----
    "hpc_slurm_node_state": lambda args, t: t.hpc_slurm_node_state(
        args["node"],
        args["target"],
        args.get("reason") or None,
        _dr(args),
        cluster=_cl(args),
    ),
    "hpc_slurm_job_hold": lambda args, t: t.hpc_slurm_job_hold(
        args["job_id"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_slurm_job_release": lambda args, t: t.hpc_slurm_job_release(
        args["job_id"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_slurm_job_requeue": lambda args, t: t.hpc_slurm_job_requeue(
        args["job_id"], cluster=_cl(args), dry_run=_dr(args)
    ),
    # hpc_slurm_job_cancel is handled specially in invoke() via _dispatch_job_cancel
    # ---- Slurm mutation (admin) ----
    "hpc_slurm_qos_modify": lambda args, t: t.hpc_slurm_qos_modify(
        args["name"],
        args.get("max_wall_min"),
        _dr(args, default=True),
        cluster=_cl(args),
    ),
    "hpc_slurm_qos_create": lambda args, t: t.hpc_slurm_qos_create(
        args["name"],
        args.get("max_wall_min"),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reservation_create": lambda args, t: t.hpc_slurm_reservation_create(
        args["name"],
        args["nodes"],
        args["start"],
        args["duration"],
        args.get("users", ""),
        args.get("accounts", ""),
        args.get("flags", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reservation_update": lambda args, t: t.hpc_slurm_reservation_update(
        args["name"],
        args.get("nodes", ""),
        args.get("start", ""),
        args.get("duration", ""),
        args.get("users", ""),
        args.get("flags", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reservation_delete": lambda args, t: t.hpc_slurm_reservation_delete(
        args["name"], cluster=_cl(args), dry_run=_dr(args)
    ),
    "hpc_slurm_partition_update": lambda args, t: t.hpc_slurm_partition_update(
        args["name"],
        args.get("state", ""),
        args.get("max_time", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    # ---- Slurm mutation (superadmin) ----
    "hpc_slurm_account_create": lambda args, t: t.hpc_slurm_account_create(
        args["name"],
        args.get("description", ""),
        args.get("organization", ""),
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_association_create": lambda args, t: t.hpc_slurm_association_create(
        args["user"],
        args["account"],
        cluster=_cl(args),
        dry_run=_dr(args),
    ),
    "hpc_slurm_reconfigure": lambda args, t: t.hpc_slurm_reconfigure(
        cluster=_cl(args), dry_run=_dr(args)
    ),
    # ---- Warewulf ----
    "hpc_warewulf_node_status": lambda args, t: t.hpc_warewulf_node_status(
        cluster=_cl(args)
    ),
    "hpc_warewulf_image_list": lambda args, t: t.hpc_warewulf_image_list(
        cluster=_cl(args)
    ),
    "hpc_warewulf_power_reset": lambda args, t: t.hpc_warewulf_power_reset(
        args["node"],
        bool(args.get("dry_run", True)),
        cluster=_cl(args),
    ),
    # ---- Spack ----
    "hpc_spack_env_list": lambda args, t: t.hpc_spack_env_list(cluster=_cl(args)),
    "hpc_spack_find": lambda args, t: t.hpc_spack_find(
        args["env"], cluster=_cl(args)
    ),
    "hpc_spack_compilers": lambda args, t: t.hpc_spack_compilers(cluster=_cl(args)),
    # ---- Ansible ----
    "hpc_ansible_playbook_run": lambda args, t: t.hpc_ansible_playbook_run(
        args["playbook"],
        args.get("limit") or None,
        bool(args.get("check", False)),
        _dr(args, default=True),
        cluster=_cl(args),
    ),
    "hpc_ansible_inventory_generate": lambda args, t: t.hpc_ansible_inventory_generate(
        cluster=_cl(args)
    ),
    # ---- Health ----
    "hpc_cluster_health_check": lambda args, t: json.dumps(
        t.hpc_cluster_health_check(cluster=_cl(args)),
        indent=2,
        default=str,
    ),
}


def _dispatch_job_cancel(
    args: dict[str, Any], tools: Any, role: Role, actor: str
) -> str:
    """Special dispatch for hpc_slurm_job_cancel — passes role and actor for ownership check."""
    result = tools.hpc_slurm_job_cancel(
        args["job_id"],
        actor=actor,
        role=role,
        cluster=args.get("cluster", "default"),
        dry_run=bool(args.get("dry_run", False)),
    )
    return result


def _dispatch_skill(name: str, args: dict[str, Any], role: Role, actor: str) -> str:
    from hpc_pilot.skills.runner import hpc_skill_describe, hpc_skill_run

    if name == "hpc_skill_describe":
        return hpc_skill_describe(args["name"])

    if name == "hpc_skill_run":
        result = hpc_skill_run(
            args["name"],
            args.get("inputs"),
            role=role,
            actor=actor,
            cluster=args.get("cluster", "default"),
            resume_run_id=args.get("resume_run_id"),
        )
        return json.dumps(result, indent=2, default=str)

    return f"[unknown tool: {name}]"


def _dispatch(name: str, args: dict[str, Any], tools: Any) -> str:
    handler = _DISPATCH.get(name)
    if handler is None:
        return f"[unknown tool: {name}]"
    return handler(args, tools)
