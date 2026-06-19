"""Centralized tool invocation: RBAC check → audit → dispatch → result string."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from hpc_pilot.audit import audit_tool
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

    Returns the tool's string result.  Raises PermissionError when the role
    is insufficient; RuntimeError or ValueError on tool-level failures.
    """
    check_permission(name, role)
    from hpc_pilot import tools

    with audit_tool(name, actor, role.value, args, dry_run=dry_run):
        result = _dispatch(name, args, tools)
    return result or "(no output)"


def _dispatch(name: str, args: dict[str, Any], tools: Any) -> str:  # noqa: PLR0911
    if name == "hpc_slurm_node_status":
        return cast(str, tools.hpc_slurm_node_status(args.get("node", "")))

    if name == "hpc_slurm_queue":
        filters = {k: v for k, v in args.items() if k in ("user", "partition", "state") and v}
        return cast(str, tools.hpc_slurm_queue(filters or None))

    if name == "hpc_slurm_node_state":
        return cast(str, tools.hpc_slurm_node_state(
            args["node"],
            args["target"],
            args.get("reason") or None,
            bool(args.get("dry_run", True)),
        ))

    if name == "hpc_slurm_qos_modify":
        return cast(str, tools.hpc_slurm_qos_modify(
            args["name"],
            args.get("max_wall_min"),
            bool(args.get("dry_run", True)),
        ))

    if name == "hpc_warewulf_node_status":
        return cast(str, tools.hpc_warewulf_node_status())

    if name == "hpc_warewulf_image_list":
        return cast(str, tools.hpc_warewulf_image_list())

    if name == "hpc_warewulf_power_reset":
        return cast(str, tools.hpc_warewulf_power_reset(args["node"], bool(args.get("dry_run", True))))

    if name == "hpc_spack_env_list":
        return cast(str, tools.hpc_spack_env_list())

    if name == "hpc_spack_find":
        return cast(str, tools.hpc_spack_find(args["env"]))

    if name == "hpc_spack_compilers":
        return cast(str, tools.hpc_spack_compilers())

    if name == "hpc_ansible_playbook_run":
        return cast(str, tools.hpc_ansible_playbook_run(
            args["playbook"],
            args.get("limit") or None,
            bool(args.get("check", False)),
            bool(args.get("dry_run", True)),
        ))

    if name == "hpc_ansible_inventory_generate":
        return cast(str, tools.hpc_ansible_inventory_generate())

    if name == "hpc_cluster_health_check":
        return json.dumps(cast(Any, tools.hpc_cluster_health_check()), indent=2, default=str)

    return f"[unknown tool: {name}]"
