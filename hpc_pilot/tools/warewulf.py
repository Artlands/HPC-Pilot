"""Warewulf tools and output parsers."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import subprocess
from typing import Any, cast

from hpc_pilot.paths import get_home
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate

# ===================================================================
# Image management
# ===================================================================


def hpc_warewulf_image_list(*, cluster: str = "default") -> str:
    """Return wwctl image list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "image", "list"], cluster=cl)


def hpc_warewulf_image_import(
    name: str, source: str, *, cluster: str = "default", dry_run: bool = False
) -> str:
    """Import a container image into Warewulf.

    ``wwctl image import <source> <name>``
    """
    _validate(name, "image name")
    _validate(source, "image source")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "image", "import", source, name],
        cluster=cl,
        timeout=300,
        dry_run=dry_run,
    )


def hpc_warewulf_image_build(
    name: str,
    base: str,
    exec_steps: list[str] | None = None,
    *,
    gpu: bool = False,
    cluster: str = "default",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Build a Warewulf container image.

    Computes spec_hash = SHA256 of (base + sorted exec_steps + gpu). If a build
    with the same hash exists in ``~/.hpc-pilot/warewulf/builds/<name>/<spec_hash>/``,
    returns cached metadata.  Otherwise runs ``wwctl image build <name>``.
    """
    _validate(name, "image name")
    _validate(base, "base image name")
    steps = sorted(exec_steps or [])
    spec_input = f"{base}|{'|'.join(steps)}|gpu={gpu}"
    spec_hash = hashlib.sha256(spec_input.encode()).hexdigest()[:16]

    build_dir = os.path.join(get_home(), "warewulf", "builds", name, spec_hash)
    log_path = os.path.join(build_dir, "build.log")

    # Check for cached build metadata
    meta_path = os.path.join(build_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path) as f:
                cached: dict[str, Any] = json.load(f)
            cached["cached"] = True
            return cached
        except (json.JSONDecodeError, OSError):
            pass  # corrupt cache -- rebuild

    cl = _resolve_cluster(cluster)
    if dry_run:
        cmd = [cl.warewulf("wwctl"), "image", "build", name]
        if gpu:
            cmd.append("--gpu")
        return {
            "name": name,
            "spec_hash": spec_hash,
            "dry_run": True,
            "command": "DRY-RUN: " + " ".join(cmd),
        }

    os.makedirs(build_dir, exist_ok=True)

    cmd = [cl.warewulf("wwctl"), "image", "build", name]
    if gpu:
        cmd.append("--gpu")

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600
        )
        with open(log_path, "w") as f:
            f.write(result.stdout)
            if result.stderr:
                f.write("\n--- stderr ---\n")
                f.write(result.stderr)
    except subprocess.TimeoutExpired as exc:
        with open(log_path, "a") as f:
            f.write("\n--- TIMEOUT after 600s ---\n")
        raise RuntimeError(f"wwctl image build {name} timed out after 600s") from exc

    if result.returncode != 0:
        raise RuntimeError(
            f"wwctl image build {name} exited {result.returncode}: "
            f"{result.stderr.strip() or '(no stderr)'}"
        )

    # Determine image size
    size_mb = 0
    image_path = os.path.join(cl.warewulf_bin_dir, "..", "var", "warewulf", "images", name)
    if os.path.isdir(image_path):
        total_bytes = 0
        for dirpath, _dirnames, filenames in os.walk(image_path):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                with contextlib.suppress(OSError):
                    total_bytes += os.path.getsize(fp)
        size_mb = total_bytes // (1024 * 1024)

    meta = {
        "name": name,
        "base": base,
        "spec_hash": spec_hash,
        "exec_steps": steps,
        "gpu": gpu,
        "size_mb": size_mb,
        "log_path": log_path,
        "cached": False,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)

    return meta


def hpc_warewulf_image_delete(
    name: str, *, cluster: str = "default", dry_run: bool = False
) -> str:
    """Delete a Warewulf image.

    ``wwctl image delete <name>``
    """
    _validate(name, "image name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "image", "delete", name],
        cluster=cl,
        dry_run=dry_run,
    )


# ===================================================================
# Node lifecycle
# ===================================================================


def hpc_warewulf_node_status(*, cluster: str = "default") -> str:
    """Return wwctl node list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "node", "list"], cluster=cl)


def hpc_warewulf_node_show(
    name: str, *, cluster: str = "default"
) -> list[dict[str, str]]:
    """Show detailed node configuration.

    ``wwctl node show <name>``, returns structured parsed output.
    """
    _validate(name, "node name")
    cl = _resolve_cluster(cluster)
    raw = _run([cl.warewulf("wwctl"), "node", "show", name], cluster=cl)
    return _parse_key_value_sections(raw)


def hpc_warewulf_node_add(
    name: str,
    mac: str,
    ipaddr: str,
    profile: str | None = None,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Add a new Warewulf node definition.

    ``wwctl node add <name> --mac=<mac> --ipaddr=<ip> [--profile=<profile>]``
    """
    _validate(name, "node name")
    _validate(mac, "MAC address")
    _validate(ipaddr, "IP address")
    cl = _resolve_cluster(cluster)
    cmd = [
        cl.warewulf("wwctl"), "node", "add", name,
        f"--mac={mac}",
        f"--ipaddr={ipaddr}",
    ]
    if profile:
        _validate(profile, "profile name")
        cmd.append(f"--profile={profile}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


def hpc_warewulf_node_set(
    name: str, *, cluster: str = "default", dry_run: bool = False, **kwargs: str
) -> str:
    """Update a Warewulf node definition.

    ``wwctl node set <name> --<key>=<value> ...``

    Accepts keyword arguments for any node property (mac, ipaddr, profile, image, etc).
    """
    _validate(name, "node name")
    cl = _resolve_cluster(cluster)
    cmd = [cl.warewulf("wwctl"), "node", "set", name]
    for key, value in kwargs.items():
        cmd.append(f"--{key}={value}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


def hpc_warewulf_node_delete(
    name: str, *, cluster: str = "default", dry_run: bool = False
) -> str:
    """Remove a Warewulf node definition.

    ``wwctl node delete <name>``
    """
    _validate(name, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "node", "delete", name],
        cluster=cl,
        dry_run=dry_run,
    )


# ===================================================================
# Power management
# ===================================================================


def hpc_warewulf_power_reset(node: str, dry_run: bool = False, *, cluster: str = "default") -> str:
    """Power-reset a Warewulf node so it PXE-boots from its assigned image."""
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "reset", node],
        cluster=cl,
        timeout=120,
        dry_run=dry_run,
    )


def hpc_warewulf_power_status(
    node: str, *, cluster: str = "default"
) -> str:
    """Return power status of a Warewulf node.

    ``wwctl power status <node>``
    """
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "status", node],
        cluster=cl,
    )


def hpc_warewulf_power_on(
    node: str, *, cluster: str = "default", dry_run: bool = False
) -> str:
    """Power on a Warewulf node.

    ``wwctl power on <node>``
    """
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "on", node],
        cluster=cl,
        dry_run=dry_run,
    )


def hpc_warewulf_power_off(
    node: str, *, cluster: str = "default", dry_run: bool = False
) -> str:
    """Power off a Warewulf node.

    ``wwctl power off <node>``
    """
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "off", node],
        cluster=cl,
        dry_run=dry_run,
    )


# ===================================================================
# Profile management
# ===================================================================


def hpc_warewulf_profile_list(*, cluster: str = "default") -> str:
    """List Warewulf profiles.

    ``wwctl profile list``
    """
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "profile", "list"], cluster=cl)


def hpc_warewulf_profile_set(
    name: str, *, cluster: str = "default", dry_run: bool = False, **kwargs: str
) -> str:
    """Update a Warewulf profile.

    ``wwctl profile set <name> --<key>=<value> ...``
    """
    _validate(name, "profile name")
    cl = _resolve_cluster(cluster)
    cmd = [cl.warewulf("wwctl"), "profile", "set", name]
    for key, value in kwargs.items():
        cmd.append(f"--{key}={value}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


# ===================================================================
# Overlay management
# ===================================================================


def _overlay_dir(overlay: str) -> str:
    """Return the local overlay staging directory path."""
    return os.path.join(get_home(), "warewulf", "overlays", overlay)


def _validate_path_safe(path: str) -> str:
    """Ensure *path* does not escape the overlay root via ``..`` or absolute paths.

    Returns the normalized relative path.
    """
    normalized = os.path.normpath(path).lstrip("/")
    if normalized.startswith("..") or normalized.startswith("/") or ".." in normalized.split(os.sep):
        raise ValueError(f"Path traversal detected: {path!r}")
    return normalized


def hpc_warewulf_overlay_list(*, cluster: str = "default") -> str:
    """List Warewulf overlays.

    ``wwctl overlay list``
    """
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "overlay", "list"], cluster=cl)


def hpc_warewulf_overlay_edit(
    overlay: str,
    path: str,
    content: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Edit a file inside a Warewulf overlay.

    Writes *content* to ``~/.hpc-pilot/warewulf/overlays/<overlay>/<path>``.
    Auto-initializes a git repo in the overlay directory for versioning.
    Commits changes, then runs ``wwctl overlay build <overlay>``.

    Returns dict with overlay, files_changed, commit, and rebuild_returncode.
    """
    _validate(overlay, "overlay name")
    safe_path = _validate_path_safe(path)

    overlay_root = _overlay_dir(overlay)
    os.makedirs(os.path.join(overlay_root, os.path.dirname(safe_path)), exist_ok=True)

    file_path = os.path.join(overlay_root, safe_path)

    if dry_run:
        return {
            "overlay": overlay,
            "path": safe_path,
            "dry_run": True,
            "overlay_root": overlay_root,
        }

    # Write content
    with open(file_path, "w") as f:
        f.write(content)

    # Auto-init git repo
    git_dir = overlay_root
    git_init = not os.path.exists(os.path.join(git_dir, ".git"))

    try:
        if git_init:
            subprocess.run(
                ["git", "init"],
                cwd=git_dir, capture_output=True, text=True, timeout=30,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "HPC Pilot"],
                cwd=git_dir, capture_output=True, text=True, timeout=30,
            )
            subprocess.run(
                ["git", "config", "user.email", "hpc-pilot@localhost"],
                cwd=git_dir, capture_output=True, text=True, timeout=30,
            )

        subprocess.run(
            ["git", "add", safe_path],
            cwd=git_dir, capture_output=True, text=True, timeout=30,
            check=True,
        )
        commit_result = subprocess.run(
            ["git", "commit", "-m", f"overlay edit: {overlay}/{safe_path}"],
            cwd=git_dir, capture_output=True, text=True, timeout=30,
        )
        if commit_result.returncode != 0:
            commit_hash = "(no changes to commit)"
        else:
            # Get short hash
            try:
                log_result = subprocess.run(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=git_dir, capture_output=True, text=True, timeout=10,
                )
                commit_hash = log_result.stdout.strip() if log_result.returncode == 0 else "(unknown)"
            except Exception:
                commit_hash = "(unknown)"

    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Git operation in overlay {overlay} failed: {exc}") from exc

    # Rebuild the overlay
    cl = _resolve_cluster(cluster)
    rebuild_returncode = 0
    try:
        _run([cl.warewulf("wwctl"), "overlay", "build", overlay], cluster=cl, timeout=120)
    except RuntimeError as exc:
        rebuild_returncode = -1
        return {
            "overlay": overlay,
            "files_changed": [safe_path],
            "commit": commit_hash,
            "rebuild_returncode": rebuild_returncode,
            "rebuild_error": str(exc),
        }

    # Count changed files
    try:
        diff_result = subprocess.run(
            ["git", "diff", "--name-status", "HEAD~1..HEAD"],
            cwd=git_dir, capture_output=True, text=True, timeout=10,
        )
        files_changed = [
            line.strip() for line in diff_result.stdout.splitlines() if line.strip()
        ] or [safe_path]
    except Exception:
        files_changed = [safe_path]

    return {
        "overlay": overlay,
        "files_changed": files_changed,
        "commit": commit_hash,
        "rebuild_returncode": rebuild_returncode,
    }


def hpc_warewulf_overlay_build(
    overlay: str, *, cluster: str = "default", dry_run: bool = False
) -> str:
    """Build a Warewulf overlay.

    ``wwctl overlay build <overlay>``
    """
    _validate(overlay, "overlay name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "overlay", "build", overlay],
        cluster=cl,
        timeout=120,
        dry_run=dry_run,
    )


def hpc_warewulf_overlay_revert(
    overlay: str, *, commit: str = "HEAD", cluster: str = "default", dry_run: bool = False
) -> dict[str, Any]:
    """Revert an overlay to a prior git commit, then rebuild.

    Performs a ``git checkout <commit>`` in the overlay directory,
    then runs ``wwctl overlay build <overlay>``.

    Returns dict with overlay, commit, and rebuild_returncode.
    """
    _validate(overlay, "overlay name")
    overlay_root = _overlay_dir(overlay)

    if not os.path.exists(os.path.join(overlay_root, ".git")):
        raise RuntimeError(
            f"Overlay {overlay} has no git history at {overlay_root}. "
            "Cannot revert without a git repository."
        )

    if dry_run:
        return {
            "overlay": overlay,
            "commit": commit,
            "dry_run": True,
            "overlay_root": overlay_root,
        }

    try:
        subprocess.run(
            ["git", "checkout", commit],
            cwd=overlay_root, capture_output=True, text=True, timeout=30,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"git checkout {commit} in overlay {overlay} failed: "
            f"{exc.stderr.strip() or exc}"
        ) from exc

    cl = _resolve_cluster(cluster)
    rebuild_returncode = 0
    try:
        _run([cl.warewulf("wwctl"), "overlay", "build", overlay], cluster=cl, timeout=120)
    except RuntimeError:
        rebuild_returncode = -1

    return {
        "overlay": overlay,
        "commit": commit,
        "rebuild_returncode": rebuild_returncode,
    }


# ===================================================================
# Server configuration (DHCP, TFTP, NFS)
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


def _apply_typed_updates(
    config: dict[str, Any], updates: dict[str, Any]
) -> bool:
    """Apply typed updates to *config* in-place.  Returns True if changed."""
    changed = False
    for key, value in updates.items():
        if value is not None:
            existing = config.get(key)
            # Preserve the existing type when possible
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
        # Even if no local config change, we still check if /etc/warewulf/warewulf.conf needs syncing
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
            f"Failed to write {etc_path}: {exc}. "
            "You may need superuser privileges."
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


def hpc_warewulf_configure_tftp(
    *, cluster: str = "default", dry_run: bool = False
) -> str:
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


def hpc_warewulf_configure_nfs(
    *, cluster: str = "default", dry_run: bool = False
) -> str:
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


# ===================================================================
# Server status
# ===================================================================


def hpc_warewulf_server_status(
    *, cluster: str = "default"
) -> dict[str, Any]:
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
        is_active = subprocess.run(
            ["systemctl", "is-active", "warewulfd"],
            capture_output=True, text=True, timeout=10,
        )
        result["systemctl_active"] = is_active.stdout.strip()
        result["systemctl_enabled"] = "unknown"
        is_enabled = subprocess.run(
            ["systemctl", "is-enabled", "warewulfd"],
            capture_output=True, text=True, timeout=10,
        )
        result["systemctl_enabled"] = is_enabled.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        result["systemctl_error"] = str(exc)

    return result


# ===================================================================
# Warewulf output parsers
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
