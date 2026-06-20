"""Prometheus metrics tools — package, see submodules for details.

Backward-compatibility re-exports so that tests patching
``hpc_pilot.tools.metrics.<name>`` continue to work.
"""

import os  # noqa: F401
import subprocess  # noqa: F401

from hpc_pilot.tools._run import _resolve_cluster, _run  # noqa: F401
from hpc_pilot.tools.metrics.prometheus import (  # noqa: F401
    _build_ssh_cmd,
    _cluster_prometheus_config,
    _cluster_prometheus_url,
    hpc_fabric_ib_link_status,
    hpc_metrics_node_summary,
    hpc_metrics_prometheus_alerts,
    hpc_metrics_prometheus_query,
    hpc_storage_lustre_status,
    hpc_storage_mounts,
)
from hpc_pilot.tools.observability.gpu import (  # noqa: F401
    hpc_gpu_dcgm_diag,
    hpc_gpu_nvidia_smi,
)
from hpc_pilot.tools.observability.logs import (  # noqa: F401
    _redact_log_line,
    _redact_output,
    hpc_logs_dmesg_xid,
    hpc_logs_search,
    hpc_logs_slurmctld_tail,
    hpc_logs_slurmd_tail,
)
