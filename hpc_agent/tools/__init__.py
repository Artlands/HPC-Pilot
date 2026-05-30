"""HPC Agent Tools Package.

This package contains tools for managing HPC clusters:
- Slurm: scheduler management
- Warewulf: provisioning
- Ansible: configuration management
- Spack: software management

All tools are registered via the @tool decorator in their respective modules.
"""

from hpc_agent.tools.ansible import (
    compose_playbook,
    lint_playbook,
    manage_inventory,
    manage_secret,
    run_playbook,
)
from hpc_agent.tools.slurm import (
    diag,
    extend_account,
    job_accounting,
    manage_qos,
    manage_reservation,
    manage_user_assoc,
    node_state,
    node_status,
    queue,
    reconfigure,
    set_limits,
    show_assoc,
    usage_report,
)
from hpc_agent.tools.spack import (
    create_view,
    find,
    generate_modules,
    install_packages,
    list_envs,
    manage_buildcache,
    manage_compilers,
    manage_environment,
    spec,
)
from hpc_agent.tools.warewulf import (
    assign_image_to_nodes,
    build_node_image,
    define_profile,
    import_container,
    list_images,
    list_nodes,
    manage_overlay,
    provision_node,
    rebuild_overlay,
    ww_node_status,
)
from hpc_agent.tools.warewulf_bootstrap import (
    configure_dhcp,
    configure_nfs,
    configure_tftp,
    server_status,
)

__all__ = [
    # Slurm tools
    "diag",
    "extend_account",
    "job_accounting",
    "manage_qos",
    "manage_reservation",
    "manage_user_assoc",
    "node_state",
    "node_status",
    "queue",
    "reconfigure",
    "set_limits",
    "show_assoc",
    "usage_report",
    "add_node_to_partition",
    "manage_partition",
    # Warewulf tools
    "assign_image_to_nodes",
    "build_node_image",
    "define_profile",
    "import_container",
    "list_images",
    "list_nodes",
    "manage_overlay",
    "provision_node",
    "rebuild_overlay",
    "ww_node_status",
    # Warewulf bootstrap tools
    "configure_dhcp",
    "configure_nfs",
    "configure_tftp",
    "server_status",
    # Ansible tools
    "compose_playbook",
    "lint_playbook",
    "manage_inventory",
    "manage_secret",
    "run_playbook",
    # Spack tools
    "create_view",
    "find",
    "generate_modules",
    "install_packages",
    "list_envs",
    "manage_buildcache",
    "manage_compilers",
    "manage_environment",
    "spec",
]
