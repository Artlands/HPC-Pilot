"""Role-Based Access Control for HPC Pilot tool invocations."""
from __future__ import annotations

import json
import os
from enum import Enum

from hpc_pilot.paths import auth_path


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    def __ge__(self, other: "Role") -> bool:  # type: ignore[override]
        _ORDER = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}
        return _ORDER[self] >= _ORDER[other]

    def __gt__(self, other: "Role") -> bool:  # type: ignore[override]
        _ORDER = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}
        return _ORDER[self] > _ORDER[other]


# Minimum role required to invoke each named tool.
TOOL_MIN_ROLE: dict[str, Role] = {
    # Read-only (VIEWER and above)
    "hpc_slurm_node_status": Role.VIEWER,
    "hpc_slurm_queue": Role.VIEWER,
    "hpc_warewulf_node_status": Role.VIEWER,
    "hpc_warewulf_image_list": Role.VIEWER,
    "hpc_spack_env_list": Role.VIEWER,
    "hpc_spack_find": Role.VIEWER,
    "hpc_spack_compilers": Role.VIEWER,
    "hpc_ansible_inventory_generate": Role.VIEWER,
    "hpc_cluster_health_check": Role.VIEWER,
    # Mutating node state (OPERATOR and above)
    "hpc_slurm_node_state": Role.OPERATOR,
    # Dangerous / cluster-wide (ADMIN only)
    "hpc_slurm_qos_modify": Role.ADMIN,
    "hpc_ansible_playbook_run": Role.ADMIN,
    "hpc_warewulf_bootstrap": Role.ADMIN,
}


def get_role() -> Role:
    """Return the current actor's role from env → auth.json → default VIEWER."""
    env_role = os.environ.get("HPC_PILOT_ROLE", "").lower()
    if env_role in {r.value for r in Role}:
        return Role(env_role)

    apath = auth_path()
    if os.path.exists(apath):
        try:
            with open(apath) as f:
                data = json.load(f)
            return Role(str(data.get("role", "viewer")).lower())
        except (json.JSONDecodeError, ValueError, KeyError, OSError):
            pass

    return Role.VIEWER


def check_permission(tool_name: str, role: Role) -> None:
    """Raise PermissionError if *role* is below the minimum required for *tool_name*."""
    required = TOOL_MIN_ROLE.get(tool_name, Role.ADMIN)
    if not (role >= required):
        raise PermissionError(
            f"Tool '{tool_name}' requires role '{required.value}'; "
            f"current role is '{role.value}'"
        )
