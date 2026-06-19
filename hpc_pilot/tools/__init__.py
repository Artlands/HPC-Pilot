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
    hpc_ansible_inventory_generate,
    hpc_ansible_playbook_run,
)
from hpc_pilot.tools.health import hpc_cluster_health_check  # noqa: F401
from hpc_pilot.tools.slurm import (  # noqa: F401
    hpc_slurm_node_state,
    hpc_slurm_node_status,
    hpc_slurm_qos_modify,
    hpc_slurm_queue,
    parse_slurm_nodes,
    parse_slurm_queue,
)
from hpc_pilot.tools.spack import (  # noqa: F401
    hpc_spack_compilers,
    hpc_spack_env_list,
    hpc_spack_find,
    parse_spack_envs,
)
from hpc_pilot.tools.warewulf import (  # noqa: F401
    hpc_warewulf_image_list,
    hpc_warewulf_node_status,
    hpc_warewulf_power_reset,
    parse_warewulf_nodes,
)
