"""Warewulf server configuration tools — DHCP, TFTP, NFS, server status, profiles, and output parsers."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from typing import Any, cast

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run


# ===================================================================
# Output parsers (shared across warewulf submodules)
# ===================================================================


def parse_warewulf_nodes(output: str) -> list[dict[str, str]]:
    """Parse ``wwctl node list`` tabular output into a list of node dicts."""
    rows: list[dict[str, str]] = []
    header: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not header:
            header = stripped.split()
            continue
        parts = stripped.split(None, len(header) - 1)
        if parts:
            rows.append(dict(zip(header, parts, strict=False)))
    return rows


def _parse_key_value_sections(output: str) -> list[dict[str, str]]:
    """Parse ``wwctl node show <name>`` key=value output into a list of dicts.

    Each section (separated by blank lines) becomes one dict entry with
    key -> value pairs parsed from ``key = value`` lines.
    """
    sections: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            if current:
                sections.append(current)
                current = {}
            continue
        if "=" in stripped and not stripped.startswith("#"):
            key, _, val = stripped.partition("=")
            current[key.strip()] = val.strip()
    if current:
        sections.append(current)
    return sections


# ===================================================================
# Config management helpers
# ===================================================================


def _warewulf_conf_path() -> str:
    """Return the path to the managed warewulf.conf copy."""
    return os.path.join(get_home(), "warewulf", "warewulf.conf")


def _read_managed_conf() -> dict[str, Any] | None:
    """Read the managed warewulf.conf JSON, or return None."""
    path = _warewulf_conf_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return cast(dict[str, Any] | None, json.load(f))
    except (json.JSONDecodeError, OSError):
        return None


def _detect_external_edit() -> str:
    """Check if /etc/warewulf/warewulf.conf was modified outside HPC Pilot.

    Returns a warning string if external edits are detected, empty string otherwise.
    """
    managed_path = _warewulf_conf_path()
    etc_path = "/etc/warewulf/warewulf.conf"

    if not os.path.exists(managed_path) or not os.path.exists(etc_path):
        return ""

    try:
        with open(managed_path, "rb") as f:
            managed_sha = hashlib.sha256(f.read()).hexdigest()
        with open(etc_path, "rb") as f:
            etc_sha = hashlib.sha256(f.read()).hexdigest()

        if managed_sha != etc_sha:
            managed_mtime = os.path.getmtime(managed_path)
            etc_mtime = os.path.getmtime(etc_path)
            if etc_mtime > managed_mtime:
                return (
                    f"WARNING: /etc/warewulf/warewulf.conf was modified outside HPC Pilot "
                    f"(last modified {etc_mtime:.0f}) vs managed copy ({managed_mtime:.0f}). "
                    f"External changes will be overwritten."
                )
    except OSError:
        pass
    return ""


def _apply_typed_updates(config: dict[str, Any], updates: dict[str, Any]) -> bool:
    """Apply typed updates to *config* in-place.  Returns True if changed."""
    changed = False
    for key, value in updates.items():
        if value is not None:
            existing = config.get(key)
            if existing is not None and not isinstance(value, type(existing)):
                try:
                    value = type(existing)(value)
                except (ValueError, TypeError):
                    raise ValueError(
                        f"Cannot convert {key}={value!r} to {type(existing).__name__}"
                    ) from None
            if existing != value:
                config[key] = value
                changed = True
        elif key in config:
            del config[key]
            changed = True
    return changed


# ===================================================================
# Profile management
# ===================================================================


@hpc_tool(
    name="hpc_warewulf_profile_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_warewulf_profile_list",
        "description": "List Warewulf profiles (wwctl profile list).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_warewulf_profile_list(*, cluster: str = "default") -> str:
    """List Warewulf profiles.

    ``wwctl profile list``
    """
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "profile", "list"], cluster=cl)


@hpc_tool(
    name="hpc_warewulf_profile_set",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_profile_set",
        "description": "Update a Warewulf profile (wwctl profile set). Pass any profile property as a keyword argument.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Profile name"},
                "image": {
                    "type": "string",
                    "description": "Default image for nodes using this profile",
                },
                "network": {"type": "string", "description": "Network configuration"},
            },
            "required": ["name"],
        },
    },
)
def hpc_warewulf_profile_set(
    name: str, *, cluster: str = "default", dry_run: bool = False, **kwargs: str
) -> str:
    """Update a Warewulf profile.

    ``wwctl profile set <name> --<key>=<value> ...``
    """
    cl = _resolve_cluster(cluster)
    cmd = [cl.warewulf("wwctl"), "profile", "set", name]
    for key, value in kwargs.items():
        cmd.append(f"--{key}={value}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


# ===================================================================
# Server configuration (DHCP, TFTP, NFS)
# ===================================================================


@hpc_tool(
    name="hpc_warewulf_configure_dhcp",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_warewulf_configure_dhcp",
        "description": "Configure Warewulf DHCP. Reads managed warewulf.conf, applies updates, copies to /etc/warewulf/warewulf.conf atomically, then runs wwctl configure dhcp. Superadmin only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "range_start": {"type": "string", "description": "DHCP range start IP"},
                "range_end": {"type": "string", "description": "DHCP range end IP"},
                "template": {"type": "string", "description": "DHCP config template path"},
            },
            "required": [],
        },
    },
)
def hpc_warewulf_configure_dhcp(
    range_start: str | None = None,
    range_end: str | None = None,
    template: str | None = None,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Configure Warewulf DHCP.

    Reads the managed ``warewulf.conf`` from ``~/.hpc-pilot/warewulf/warewulf.conf``,
    applies typed updates (range_start, range_end, template), copies to
    ``/etc/warewulf/warewulf.conf`` if changed (atomic write), then runs
    ``wwctl configure dhcp``.

    Returns dict with ``changed`` (bool), ``sha256``, and an optional
    ``external_edit_warning`` field if warewulf.conf was modified outside HPC Pilot.
    """
    cl = _resolve_cluster(cluster)

    # External-edit detection
    ext_warning = _detect_external_edit()

    # Read or create managed config
    config = _read_managed_conf()
    if config is None:
        config = {"dhcp": {}}
    dhcp = config.setdefault("dhcp", {})

    updates: dict[str, Any] = {}
    if range_start is not None:
        updates["range_start"] = range_start
    if range_end is not None:
        updates["range_end"] = range_end
    if template is not None:
        updates["template"] = template

    changed = _apply_typed_updates(dhcp, updates)

    if not changed and not dry_run:
        managed_conf_path = _warewulf_conf_path()
        if not os.path.exists(managed_conf_path):
            changed = True

    if dry_run:
        return {
            "changed": changed,
            "sha256": "",
            "dry_run": True,
            "dhcp_updates": updates,
        }

    # Write updated config to managed location
    managed_conf_path = _warewulf_conf_path()
    os.makedirs(os.path.dirname(managed_conf_path), exist_ok=True)
    with open(managed_conf_path, "w") as f:
        json.dump(config, f, indent=2)

    # Compute sha256
    with open(managed_conf_path, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()

    # Atomic copy to /etc/warewulf/warewulf.conf if changed
    etc_path = "/etc/warewulf/warewulf.conf"
    etc_changed = False
    try:
        if os.path.exists(etc_path):
            with open(etc_path, "rb") as f:
                existing_sha = hashlib.sha256(f.read()).hexdigest()
            etc_changed = existing_sha != sha256
        else:
            etc_changed = True

        if etc_changed:
            # Atomic write via temp file + rename
            tmp_path = etc_path + ".tmp"
            shutil.copy2(managed_conf_path, tmp_path)
            os.rename(tmp_path, etc_path)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to write {etc_path}: {exc}. " "You may need superuser privileges."
        ) from exc

    # Run wwctl configure dhcp
    _run(
        [cl.warewulf("wwctl"), "configure", "dhcp"],
        cluster=cl,
        timeout=120,
    )

    result: dict[str, Any] = {
        "changed": changed or etc_changed,
        "sha256": sha256,
    }
    if ext_warning:
        result["external_edit_warning"] = ext_warning
    return result


@hpc_tool(
    name="hpc_warewulf_configure_tftp",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_warewulf_configure_tftp",
        "description": "Configure Warewulf TFTP (wwctl configure tftp). Superadmin only.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_warewulf_configure_tftp(*, cluster: str = "default", dry_run: bool = False) -> str:
    """Configure Warewulf TFTP.

    ``wwctl configure tftp``
    """
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "configure", "tftp"],
        cluster=cl,
        timeout=120,
        dry_run=dry_run,
    )


@hpc_tool(
    name="hpc_warewulf_configure_nfs",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_warewulf_configure_nfs",
        "description": "Configure Warewulf NFS exports (wwctl configure nfs). Superadmin only.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_warewulf_configure_nfs(*, cluster: str = "default", dry_run: bool = False) -> str:
    """Configure Warewulf NFS exports.

    ``wwctl configure nfs``
    """
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "configure", "nfs"],
        cluster=cl,
        timeout=120,
        dry_run=dry_run,
    )


@hpc_tool(
    name="hpc_warewulf_server_status",
    role=Role.VIEWER,
    schema={
        "name": "hpc_warewulf_server_status",
        "description": "Return Warewulf server status (wwctl server status + systemctl is-active warewulfd).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_warewulf_server_status(*, cluster: str = "default") -> dict[str, Any]:
    """Return Warewulf server status.

    Runs ``wwctl server status`` and attempts ``systemctl is-active warewulfd``.
    Returns a dict with status info.
    """
    cl = _resolve_cluster(cluster)
    result: dict[str, Any] = {}

    try:
        wwctl_out = _run(
            [cl.warewulf("wwctl"), "server", "status"],
            cluster=cl,
            timeout=30,
        )
        result["wwctl_server_status"] = wwctl_out.strip()
    except RuntimeError as exc:
        result["wwctl_server_status"] = f"error: {exc}"

    # Check systemd service status
    try:
        result["systemctl_active"] = _run(["systemctl", "is-active", "warewulfd"], timeout=10).strip()
        result["systemctl_enabled"] = _run(["systemctl", "is-enabled", "warewulfd"], timeout=10).strip()
    except (RuntimeError, FileNotFoundError, OSError) as exc:
        result["systemctl_error"] = str(exc)

    return result
