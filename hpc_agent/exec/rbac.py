"""Role-based access control. See spec 00 §6.

Capabilities are "domain.tool" strings (e.g. "slurm.manage_qos"). Role grants use globs.
"""

from __future__ import annotations

import fnmatch
from enum import StrEnum


class Role(StrEnum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


ROLE_CAPS: dict[Role, set[str]] = {
    Role.VIEWER: {
        "*.query*",
        "*.list*",
        "*.status*",
        "*.show_*",
        "slurm.queue",
        "slurm.usage_report",
        "slurm.job_accounting",
        "slurm.diag",
    },
    Role.OPERATOR: {
        "slurm.*",
        "warewulf.rebuild_overlay",
        "warewulf.query_*",
        "warewulf.node_status",
        "ansible.run_playbook",
        "ansible.lint_playbook",
        "ansible.manage_inventory",
        "spack.query_*",
        "spack.generate_modules",
    },
    Role.ADMIN: {"*"},
}


def authorize(actor_role: Role, capability: str) -> bool:
    return any(fnmatch.fnmatch(capability, pattern) for pattern in ROLE_CAPS.get(actor_role, set()))
