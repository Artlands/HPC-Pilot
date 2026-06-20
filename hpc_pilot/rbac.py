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


# TOOL_MIN_ROLE is now derived from the @hpc_tool canonical registry.
# See hpc_pilot.tools._registry.get_tool_min_role().
TOOL_MIN_ROLE: dict[str, Role] = {}
_RBAC_LOADED: bool = False


def _ensure_role_map() -> None:
    """Lazily populate TOOL_MIN_ROLE from the canonical registry on first use."""
    global _RBAC_LOADED, TOOL_MIN_ROLE  # noqa: PLW0603
    if _RBAC_LOADED:
        return
    from hpc_pilot.tools._registry import get_tool_min_role

    TOOL_MIN_ROLE.clear()
    TOOL_MIN_ROLE.update(get_tool_min_role())
    _RBAC_LOADED = True


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
    _ensure_role_map()
    required = TOOL_MIN_ROLE.get(tool_name, Role.SUPERADMIN)
    if not (role >= required):
        raise PermissionError(
            f"Tool '{tool_name}' requires role '{required.value}'; "
            f"current role is '{role.value}'"
        )
