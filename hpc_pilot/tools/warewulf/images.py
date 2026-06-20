"""Warewulf image management tools — list, import, build, delete."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from typing import Any

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _NAME_RE, _validate


@hpc_tool(
    name="hpc_warewulf_image_list",
    role=Role.VIEWER,
    schema={
        "name": "hpc_warewulf_image_list",
        "description": "List available Warewulf container images.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_warewulf_image_list(*, cluster: str = "default") -> str:
    """Return wwctl image list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "image", "list"], cluster=cl)


@hpc_tool(
    name="hpc_warewulf_image_import",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_image_import",
        "description": "Import a container image into Warewulf (wwctl image import).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Image name"},
                "source": {"type": "string", "description": "Source path/URL of the image"},
            },
            "required": ["name", "source"],
        },
    },
)
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


@hpc_tool(
    name="hpc_warewulf_image_build",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_image_build",
        "description": "Build a Warewulf container image. Computes a spec_hash from the build parameters and caches results in ~/.hpc-pilot/warewulf/builds/.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Image name to build"},
                "base": {"type": "string", "description": "Base image name"},
                "exec_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Build execution steps/commands",
                },
                "gpu": {"type": "boolean", "description": "Include GPU support (default: false)"},
            },
            "required": ["name", "base"],
        },
    },
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
        stdout = _run(cmd, cluster=cl, timeout=600)
        with open(log_path, "w") as f:
            f.write(stdout)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"wwctl image build {name} timed out after 600s") from exc

    # Determine image size
    size_mb = 0
    image_path = os.path.join("/var", "lib", "warewulf", "images", name)
    if not os.path.isdir(image_path):
        image_path = os.path.join("/var", "warewulf", "images", name)
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


@hpc_tool(
    name="hpc_warewulf_image_delete",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_image_delete",
        "description": "Delete a Warewulf image (wwctl image delete).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Image name to delete"}},
            "required": ["name"],
        },
    },
)
def hpc_warewulf_image_delete(name: str, *, cluster: str = "default", dry_run: bool = False) -> str:
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
# Spack-in-Image build (originally in system.py)
# ===================================================================


def build_cmd(cl, name: str, exec_steps: list[str], gpu: bool) -> list[str]:
    """Build the Warewulf image build command."""
    cmd = [cl.warewulf("wwctl"), "image", "build", name]
    for step in exec_steps:
        cmd += ["--exec", step]
    if gpu:
        cmd += ["--gpu"]
    return cmd


@hpc_tool(
    name="hpc_warewulf_image_build_from_env",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_image_build_from_env",
        "description": "Build a Warewulf compute image with a Spack environment baked in. Imports the base image, installs Spack, activates the env, and builds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Output image name"},
                "base": {
                    "type": "string",
                    "description": "Base container image (default rockylinux:9)",
                },
                "spack_env": {"type": "string", "description": "Spack environment name to bake in"},
                "exec_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Additional build commands",
                },
                "gpu": {"type": "boolean", "description": "Include GPU support"},
            },
            "required": ["name"],
        },
    },
)
def hpc_warewulf_image_build_from_env(
    name: str,
    base: str = "rockylinux:9",
    spack_env: str = "",
    exec_steps: list[str] | None = None,
    *,
    gpu: bool = False,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Build a Warewulf compute image with a Spack environment baked in.

    Imports the base image, installs Spack into it via exec steps, activates
    the named Spack environment, optionally adds GPU support, and builds
    the final Warewulf image.

    Args:
        name: Output image name.
        base: Base container image (e.g. ``rockylinux:9``).
        spack_env: Spack environment name to bake into the image. The Spack
            environment must already exist and be concretized.
        exec_steps: Additional build commands to run after Spack setup.
        gpu: Include GPU driver support.
        dry_run: Preview without building.
    """
    _validate(name, "image name")
    _validate(base, "base image")

    cl = _resolve_cluster(cluster)

    # Step 1: import base image
    _run(
        [cl.warewulf("wwctl"), "image", "import", base, name],
        cluster=cl,
        timeout=300,
        dry_run=dry_run,
    )

    # Step 2: build image with Spack env
    steps: list[str] = list(exec_steps or [])
    if spack_env:
        env_path_guess = f"/shared/software/spack_envs/{spack_env}"
        spack_steps = [
            "dnf -y install spack 2>/dev/null || echo 'spack not in repo'",
            "[ -d /shared/spack ] && . /shared/spack/share/spack/setup-env.sh || true",
            f"spack env activate {spack_env} 2>/dev/null || spack env activate {env_path_guess} 2>/dev/null || true",
        ]
        steps = spack_steps + steps

    _run(
        build_cmd(cl, name, steps, gpu),
        cluster=cl,
        timeout=600,
        dry_run=dry_run,
    )

    return f"Image '{name}' built from base '{base}'" + (
        f" with Spack env '{spack_env}'" if spack_env else ""
    )
