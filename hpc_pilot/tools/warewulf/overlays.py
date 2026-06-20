"""Warewulf overlay management tools — list, edit, build, revert."""

from __future__ import annotations

import os
from typing import Any

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate


def _overlay_dir(overlay: str) -> str:
    """Return the local overlay staging directory path."""
    return os.path.join(get_home(), "warewulf", "overlays", overlay)


def _validate_path_safe(path: str) -> str:
    """Ensure *path* does not escape the overlay root via ``..`` or absolute paths.

    Returns the normalized relative path.
    """
    normalized = os.path.normpath(path).lstrip("/")
    if (
        normalized.startswith("..")
        or normalized.startswith("/")
        or ".." in normalized.split(os.sep)
    ):
        raise ValueError(f"Path traversal detected: {path!r}")
    return normalized


@hpc_tool(
    name="hpc_warewulf_overlay_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_warewulf_overlay_list",
        "description": "List Warewulf overlays (wwctl overlay list).",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_warewulf_overlay_list(*, cluster: str = "default") -> str:
    """List Warewulf overlays.

    ``wwctl overlay list``
    """
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "overlay", "list"], cluster=cl)


@hpc_tool(
    name="hpc_warewulf_overlay_edit",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_overlay_edit",
        "description": "Edit a file inside a Warewulf overlay. Writes content to the overlay staging directory, commits to git, and rebuilds the overlay. Returns status dict.",
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string", "description": "Overlay name"},
                "path": {"type": "string", "description": "File path within the overlay"},
                "content": {"type": "string", "description": "File content to write"},
            },
            "required": ["overlay", "path", "content"],
        },
    },
)
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
            _run(["git", "init"], cwd=git_dir, timeout=30)
            _run(["git", "config", "user.name", "HPC Pilot"], cwd=git_dir, timeout=30)
            _run(["git", "config", "user.email", "hpc-pilot@localhost"], cwd=git_dir, timeout=30)

        _run(["git", "add", safe_path], cwd=git_dir, timeout=30)
        try:
            _run(["git", "commit", "-m", f"overlay edit: {overlay}/{safe_path}"], cwd=git_dir, timeout=30)
            commit_hash = _run(["git", "rev-parse", "--short", "HEAD"], cwd=git_dir, timeout=10).strip()
        except RuntimeError:
            commit_hash = "(no changes to commit)"

    except RuntimeError as exc:
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
        diff_output = _run(["git", "diff", "--name-status", "HEAD~1..HEAD"], cwd=git_dir, timeout=10)
        files_changed = [
            line.strip() for line in diff_output.splitlines() if line.strip()
        ] or [safe_path]
    except Exception:
        files_changed = [safe_path]

    return {
        "overlay": overlay,
        "files_changed": files_changed,
        "commit": commit_hash,
        "rebuild_returncode": rebuild_returncode,
    }


@hpc_tool(
    name="hpc_warewulf_overlay_build",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_warewulf_overlay_build",
        "description": "Build a Warewulf overlay (wwctl overlay build).",
        "input_schema": {
            "type": "object",
            "properties": {"overlay": {"type": "string", "description": "Overlay name"}},
            "required": ["overlay"],
        },
    },
)
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


@hpc_tool(
    name="hpc_warewulf_overlay_revert",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_overlay_revert",
        "description": "Revert an overlay to a prior git commit and rebuild. The overlay must have git history (created by overlay_edit).",
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string", "description": "Overlay name"},
                "commit": {
                    "type": "string",
                    "description": "Git commit ref to revert to (default: HEAD)",
                },
            },
            "required": ["overlay"],
        },
    },
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
        _run(["git", "checkout", commit], cwd=overlay_root, timeout=30)
    except RuntimeError as exc:
        raise RuntimeError(
            f"git checkout {commit} in overlay {overlay} failed: {exc}"
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
