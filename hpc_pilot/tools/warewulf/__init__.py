"""Warewulf tools package — submodules for images, nodes, power, overlays, and services.

Backward-compatibility re-exports so that tests patching
``hpc_pilot.tools.warewulf.<name>`` continue to work.
"""

import json  # noqa: F401
import os  # noqa: F401
import subprocess  # noqa: F401

from hpc_pilot.tools._run import _resolve_cluster, _run  # noqa: F401

from hpc_pilot.tools.warewulf.images import (  # noqa: F401
    build_cmd,
    hpc_warewulf_image_build,
    hpc_warewulf_image_build_from_env,
    hpc_warewulf_image_delete,
    hpc_warewulf_image_import,
    hpc_warewulf_image_list,
)
from hpc_pilot.tools.warewulf.nodes import (  # noqa: F401
    hpc_warewulf_node_add,
    hpc_warewulf_node_add_bulk,
    hpc_warewulf_node_delete,
    hpc_warewulf_node_set,
    hpc_warewulf_node_show,
    hpc_warewulf_node_status,
)
from hpc_pilot.tools.warewulf.overlays import (  # noqa: F401
    _overlay_dir,
    _validate_path_safe,
    hpc_warewulf_overlay_build,
    hpc_warewulf_overlay_edit,
    hpc_warewulf_overlay_list,
    hpc_warewulf_overlay_revert,
)
from hpc_pilot.tools.warewulf.power import (  # noqa: F401
    hpc_warewulf_power_off,
    hpc_warewulf_power_on,
    hpc_warewulf_power_reset,
    hpc_warewulf_power_status,
)
from hpc_pilot.tools.warewulf.services import (  # noqa: F401
    _apply_typed_updates,
    _detect_external_edit,
    _parse_key_value_sections,
    _read_managed_conf,
    _warewulf_conf_path,
    hpc_warewulf_configure_dhcp,
    hpc_warewulf_configure_nfs,
    hpc_warewulf_configure_tftp,
    hpc_warewulf_profile_list,
    hpc_warewulf_profile_set,
    hpc_warewulf_server_status,
    parse_warewulf_nodes,
)
