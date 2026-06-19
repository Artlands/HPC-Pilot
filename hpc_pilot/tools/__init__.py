"""HPC cluster management tools — re-exports for backward compatibility.

All hpc_* tool functions are importable directly from ``hpc_pilot.tools``.
Internal helpers (_run, _validate, check_*) are also re-exported so that
existing test patches targeting ``hpc_pilot.tools.<name>`` continue to work.
"""
from __future__ import annotations

# Make subprocess patchable at hpc_pilot.tools.subprocess
import subprocess  # noqa: F401

from hpc_pilot.skills.runner import (  # noqa: F401
    hpc_skill_describe,
    hpc_skill_run,
)
from hpc_pilot.tools._run import (  # noqa: F401
    _resolve_cluster,
    _run,
    check_ansible_available,
    check_slurm_available,
    check_spack_available,
    check_warewulf_available,
)
from hpc_pilot.tools._validation import (  # noqa: F401
    _NAME_RE,
    _USER_RE,
    _shquote,
    _validate,
)
from hpc_pilot.tools.ansible import (  # noqa: F401
    hpc_ansible_drift_check,
    hpc_ansible_inventory_from_truth,
    hpc_ansible_inventory_generate,
    hpc_ansible_playbook_check,
    hpc_ansible_playbook_list,
    hpc_ansible_playbook_run,
    hpc_ansible_role_list,
    hpc_ansible_run_history,
    hpc_ansible_vault_decrypt,
)
from hpc_pilot.tools.health import hpc_cluster_health_check  # noqa: F401
from hpc_pilot.tools.jobs import hpc_job_logs, hpc_job_status  # noqa: F401
from hpc_pilot.tools.multi import hpc_multi_query  # noqa: F401
from hpc_pilot.tools.metrics import (  # noqa: F401
    _cluster_prometheus_url,
    _redact_log_line,
    _redact_output,
    hpc_fabric_ib_link_status,
    hpc_gpu_dcgm_diag,
    hpc_gpu_nvidia_smi,
    hpc_logs_dmesg_xid,
    hpc_logs_search,
    hpc_logs_slurmctld_tail,
    hpc_logs_slurmd_tail,
    hpc_metrics_node_summary,
    hpc_metrics_prometheus_alerts,
    hpc_metrics_prometheus_query,
    hpc_storage_lustre_status,
    hpc_storage_mounts,
)
from hpc_pilot.tools.slurm import (  # noqa: F401
    hpc_slurm_account_create,
    hpc_slurm_account_list,
    hpc_slurm_accounting,
    hpc_slurm_association_create,
    hpc_slurm_association_list,
    hpc_slurm_config_show,
    hpc_slurm_fairshare,
    hpc_slurm_job_cancel,
    hpc_slurm_job_hold,
    hpc_slurm_job_release,
    hpc_slurm_job_requeue,
    hpc_slurm_job_status,
    hpc_slurm_node_state,
    hpc_slurm_node_status,
    hpc_slurm_partition_list,
    hpc_slurm_partition_update,
    hpc_slurm_qos_create,
    hpc_slurm_qos_list,
    hpc_slurm_qos_modify,
    hpc_slurm_queue,
    hpc_slurm_reconfigure,
    hpc_slurm_reservation_create,
    hpc_slurm_reservation_delete,
    hpc_slurm_reservation_list,
    hpc_slurm_reservation_update,
    hpc_slurm_sdiag,
    hpc_slurm_usage_report,
    parse_node_state_histogram,
    parse_slurm_nodes,
    parse_slurm_queue,
)
from hpc_pilot.tools.slurm_parsers import (  # noqa: F401
    parse_reservations,
    parse_sacct,
    parse_sdiag,
    parse_squeue_long,
    parse_sshare,
)
from hpc_pilot.tools.spack import (  # noqa: F401
    hpc_spack_buildcache_push,
    hpc_spack_buildcache_update_index,
    hpc_spack_compiler_find,
    hpc_spack_compilers,
    hpc_spack_env_concretize,
    hpc_spack_env_create,
    hpc_spack_env_delete,
    hpc_spack_env_install,
    hpc_spack_env_list,
    hpc_spack_env_status,
    hpc_spack_find,
    hpc_spack_install_spec,
    hpc_spack_mirror_add,
    hpc_spack_mirror_list,
    hpc_spack_module_refresh,
    hpc_spack_uninstall,
    parse_spack_envs,
)
from hpc_pilot.tools.warewulf import (  # noqa: F401
    hpc_warewulf_configure_dhcp,
    hpc_warewulf_configure_nfs,
    hpc_warewulf_configure_tftp,
    hpc_warewulf_image_build,
    hpc_warewulf_image_delete,
    hpc_warewulf_image_import,
    hpc_warewulf_image_list,
    hpc_warewulf_node_add,
    hpc_warewulf_node_delete,
    hpc_warewulf_node_set,
    hpc_warewulf_node_show,
    hpc_warewulf_node_status,
    hpc_warewulf_overlay_build,
    hpc_warewulf_overlay_edit,
    hpc_warewulf_overlay_list,
    hpc_warewulf_overlay_revert,
    hpc_warewulf_power_off,
    hpc_warewulf_power_on,
    hpc_warewulf_power_reset,
    hpc_warewulf_power_status,
    hpc_warewulf_profile_list,
    hpc_warewulf_profile_set,
    hpc_warewulf_server_status,
    parse_warewulf_nodes,
)
