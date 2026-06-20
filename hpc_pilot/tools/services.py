"""Slurm and system service lifecycle tools."""

from __future__ import annotations

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run

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
