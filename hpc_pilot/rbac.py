"""Role-Based Access Control for HPC Pilot tool invocations."""
from __future__ import annotations

import json
import os
from enum import Enum

from hpc_pilot.paths import auth_path

_ORDER = {
    "viewer": 0,
    "operator": 1,
    "admin": 2,
    "superadmin": 3,
}


class Role(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"

    def _level(self) -> int:
        return _ORDER[self.value]

    def __lt__(self, other: Role) -> bool:  # type: ignore[override]
        return self._level() < other._level()

    def __le__(self, other: Role) -> bool:  # type: ignore[override]
        return self._level() <= other._level()

    def __ge__(self, other: Role) -> bool:  # type: ignore[override]
        return self._level() >= other._level()

    def __gt__(self, other: Role) -> bool:  # type: ignore[override]
        return self._level() > other._level()


# Minimum role required to invoke each named tool.
TOOL_MIN_ROLE: dict[str, Role] = {
    # Read-only (VIEWER and above)
    "hpc_slurm_node_status": Role.VIEWER,
    "hpc_metrics_prometheus_query": Role.VIEWER,
    "hpc_metrics_prometheus_alerts": Role.VIEWER,
    "hpc_metrics_node_summary": Role.VIEWER,
    "hpc_storage_mounts": Role.VIEWER,
    "hpc_logs_search": Role.VIEWER,
    "hpc_slurm_queue": Role.VIEWER,
    "hpc_slurm_job_status": Role.VIEWER,
    "hpc_slurm_reservation_list": Role.VIEWER,
    "hpc_slurm_partition_list": Role.VIEWER,
    "hpc_slurm_account_list": Role.VIEWER,
    "hpc_slurm_association_list": Role.VIEWER,
    "hpc_slurm_qos_list": Role.VIEWER,
    "hpc_slurm_fairshare": Role.VIEWER,
    "hpc_slurm_accounting": Role.VIEWER,
    "hpc_slurm_usage_report": Role.VIEWER,
    "hpc_slurm_sdiag": Role.VIEWER,
    "hpc_slurm_config_show": Role.VIEWER,
    "hpc_warewulf_node_status": Role.VIEWER,
    "hpc_warewulf_node_show": Role.VIEWER,
    "hpc_warewulf_image_list": Role.VIEWER,
    "hpc_warewulf_profile_list": Role.VIEWER,
    "hpc_warewulf_overlay_list": Role.VIEWER,
    "hpc_warewulf_server_status": Role.VIEWER,
    "hpc_warewulf_power_status": Role.VIEWER,
    "hpc_spack_env_list": Role.VIEWER,
    "hpc_spack_find": Role.VIEWER,
    "hpc_spack_compilers": Role.VIEWER,
    "hpc_spack_env_status": Role.VIEWER,
    "hpc_spack_mirror_list": Role.VIEWER,
    "hpc_job_status": Role.VIEWER,
    "hpc_job_logs": Role.VIEWER,
    "hpc_ansible_inventory_generate": Role.VIEWER,
    "hpc_ansible_playbook_list": Role.VIEWER,
    "hpc_ansible_role_list": Role.VIEWER,
    "hpc_ansible_run_history": Role.VIEWER,
    "hpc_cluster_health_check": Role.VIEWER,
    "hpc_skill_describe": Role.VIEWER,
    "hpc_multi_query": Role.VIEWER,
    # Mutating (OPERATOR and above)
    "hpc_slurm_node_state": Role.OPERATOR,
    "hpc_slurm_job_hold": Role.OPERATOR,
    "hpc_slurm_job_release": Role.OPERATOR,
    "hpc_slurm_job_requeue": Role.OPERATOR,
    "hpc_slurm_job_cancel": Role.OPERATOR,
    "hpc_skill_run": Role.OPERATOR,
    "hpc_ansible_drift_check": Role.OPERATOR,
    "hpc_warewulf_overlay_build": Role.OPERATOR,
    "hpc_gpu_nvidia_smi": Role.OPERATOR,
    "hpc_storage_lustre_status": Role.OPERATOR,
    "hpc_fabric_ib_link_status": Role.OPERATOR,
    "hpc_logs_slurmctld_tail": Role.OPERATOR,
    "hpc_logs_slurmd_tail": Role.OPERATOR,
    "hpc_logs_dmesg_xid": Role.OPERATOR,
    # Dangerous / cluster-wide (ADMIN and above)
    "hpc_slurm_qos_modify": Role.ADMIN,
    "hpc_slurm_qos_create": Role.ADMIN,
    "hpc_slurm_reservation_create": Role.ADMIN,
    "hpc_slurm_reservation_update": Role.ADMIN,
    "hpc_slurm_reservation_delete": Role.ADMIN,
    "hpc_slurm_partition_update": Role.ADMIN,
    "hpc_ansible_playbook_run": Role.ADMIN,
    "hpc_ansible_playbook_check": Role.ADMIN,
    "hpc_ansible_inventory_from_truth": Role.ADMIN,
    "hpc_ansible_vault_decrypt": Role.ADMIN,
    "hpc_warewulf_power_reset": Role.ADMIN,
    "hpc_warewulf_image_import": Role.ADMIN,
    "hpc_warewulf_image_build": Role.ADMIN,
    "hpc_warewulf_image_delete": Role.ADMIN,
    "hpc_warewulf_node_add": Role.ADMIN,
    "hpc_warewulf_node_add_bulk": Role.ADMIN,
    "hpc_warewulf_node_set": Role.ADMIN,
    "hpc_warewulf_node_delete": Role.ADMIN,
    "hpc_warewulf_profile_set": Role.ADMIN,
    "hpc_warewulf_overlay_edit": Role.ADMIN,
    "hpc_warewulf_overlay_revert": Role.ADMIN,
    "hpc_warewulf_power_on": Role.ADMIN,
    "hpc_warewulf_power_off": Role.ADMIN,
    "hpc_spack_env_create": Role.ADMIN,
    "hpc_spack_env_delete": Role.ADMIN,
    "hpc_spack_env_concretize": Role.ADMIN,
    "hpc_spack_env_install": Role.ADMIN,
    "hpc_spack_install_spec": Role.ADMIN,
    "hpc_spack_uninstall": Role.ADMIN,
    "hpc_spack_buildcache_push": Role.ADMIN,
    "hpc_spack_buildcache_update_index": Role.ADMIN,
    "hpc_spack_module_refresh": Role.ADMIN,
    "hpc_spack_compiler_find": Role.ADMIN,
    "hpc_gpu_dcgm_diag": Role.ADMIN,
    # Accounting schema / bootstrap (SUPERADMIN only)
    "hpc_slurm_account_create": Role.SUPERADMIN,
    "hpc_slurm_association_create": Role.SUPERADMIN,
    "hpc_slurm_reconfigure": Role.SUPERADMIN,
    "hpc_spack_mirror_add": Role.SUPERADMIN,
    "hpc_warewulf_configure_dhcp": Role.SUPERADMIN,
    "hpc_warewulf_configure_tftp": Role.SUPERADMIN,
    "hpc_warewulf_configure_nfs": Role.SUPERADMIN,
    # System & admin tools
    "hpc_audit_query": Role.VIEWER,
    "hpc_slurm_service": Role.ADMIN,
    "hpc_system_user_add": Role.ADMIN,
    "hpc_system_user_delete": Role.ADMIN,
    "hpc_system_user_group_add": Role.ADMIN,
    "hpc_system_ssh_key_deploy": Role.ADMIN,
    "hpc_login_node_processes": Role.OPERATOR,
    "hpc_storage_large_files": Role.VIEWER,
    "hpc_storage_quota_check": Role.VIEWER,
    "hpc_config_backup": Role.ADMIN,
    # Additional system tools (Phase 3.6)
    "hpc_usage_vs_budget": Role.VIEWER,
    "hpc_notify": Role.OPERATOR,
    "hpc_warewulf_image_build_from_env": Role.ADMIN,
    "hpc_job_submit_test": Role.OPERATOR,
    "hpc_storage_lustre_balance": Role.VIEWER,
    "hpc_storage_scrub_orphans": Role.ADMIN,
    "hpc_slurm_job_step_metrics": Role.VIEWER,
    "hpc_multi_migration_plan": Role.VIEWER,
    # Self-evolve meta-tools
    "hpc_self_evolve": Role.SUPERADMIN,
    "hpc_self_evolve_create_pr": Role.SUPERADMIN,
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
