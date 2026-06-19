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


_DISPATCH: dict[str, Callable[[dict[str, Any], Any], str]] = {
    "hpc_slurm_node_status": lambda args, t: t.hpc_slurm_node_status(
        args.get("node", ""), cluster=args.get("cluster", "default")
    ),
    "hpc_slurm_queue": lambda args, t: t.hpc_slurm_queue(
        {k: v for k, v in args.items() if k in ("user", "partition", "state") and v} or None,
        cluster=args.get("cluster", "default"),
    ),
    "hpc_slurm_node_state": lambda args, t: t.hpc_slurm_node_state(
        args["node"],
        args["target"],
        args.get("reason") or None,
        bool(args.get("dry_run", True)),
        cluster=args.get("cluster", "default"),
    ),
    "hpc_slurm_qos_modify": lambda args, t: t.hpc_slurm_qos_modify(
        args["name"],
        args.get("max_wall_min"),
        bool(args.get("dry_run", True)),
        cluster=args.get("cluster", "default"),
    ),
    "hpc_warewulf_node_status": lambda args, t: t.hpc_warewulf_node_status(
        cluster=args.get("cluster", "default")
    ),
    "hpc_warewulf_image_list": lambda args, t: t.hpc_warewulf_image_list(
        cluster=args.get("cluster", "default")
    ),
    "hpc_warewulf_power_reset": lambda args, t: t.hpc_warewulf_power_reset(
        args["node"],
        bool(args.get("dry_run", True)),
        cluster=args.get("cluster", "default"),
    ),
    "hpc_spack_env_list": lambda args, t: t.hpc_spack_env_list(
        cluster=args.get("cluster", "default")
    ),
    "hpc_spack_find": lambda args, t: t.hpc_spack_find(
        args["env"], cluster=args.get("cluster", "default")
    ),
    "hpc_spack_compilers": lambda args, t: t.hpc_spack_compilers(
        cluster=args.get("cluster", "default")
    ),
    "hpc_ansible_playbook_run": lambda args, t: t.hpc_ansible_playbook_run(
        args["playbook"],
        args.get("limit") or None,
        bool(args.get("check", False)),
        bool(args.get("dry_run", True)),
        cluster=args.get("cluster", "default"),
    ),
    "hpc_ansible_inventory_generate": lambda args, t: t.hpc_ansible_inventory_generate(
        cluster=args.get("cluster", "default")
    ),
    "hpc_cluster_health_check": lambda args, t: json.dumps(
        t.hpc_cluster_health_check(cluster=args.get("cluster", "default")),
        indent=2,
        default=str,
    ),
}


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
