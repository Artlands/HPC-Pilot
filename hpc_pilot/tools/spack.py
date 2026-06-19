"""Spack tools and output parsers."""
from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _SPACK_ENV_RE, _validate


def hpc_spack_env_list(*, cluster: str = "default") -> str:
    """Return spack env list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "env", "list"], cluster=cl)


def hpc_spack_find(env: str, *, cluster: str = "default") -> str:
    """Return installed specs in a Spack environment."""
    _validate(env, "environment name", _SPACK_ENV_RE)
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "find", "-l", "-N", "-d", "-e", env], cluster=cl, timeout=60)


def hpc_spack_compilers(*, cluster: str = "default") -> str:
    """Return the list of available Spack compilers."""
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "compilers"], cluster=cl)


# ---------------------------------------------------------------------------
# Phase 3: Environment lifecycle
# ---------------------------------------------------------------------------


def hpc_spack_env_create(name: str, manifest: str | None = None, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Create a new Spack environment."""
    _validate(name, "environment name", _SPACK_ENV_RE)
    cl = _resolve_cluster(cluster)
    cmd = [cl.spack(), "env", "create", name]
    if manifest:
        cmd.append(manifest)
    return _run(cmd, cluster=cl, dry_run=dry_run)


def hpc_spack_env_delete(name: str, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Delete a Spack environment."""
    _validate(name, "environment name", _SPACK_ENV_RE)
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "env", "remove", name], cluster=cl, dry_run=dry_run)


def hpc_spack_env_concretize(env: str, *, cluster: str = "default", dry_run: bool = False) -> dict[str, Any]:
    """Concretize a Spack environment and return the lockfile diff."""
    _validate(env, "environment name", _SPACK_ENV_RE)
    cl = _resolve_cluster(cluster)
    spack = cl.spack()
    spack_root = cl.spack_root

    # Find the spack.lock location
    lock_path = os.path.join(spack_root, "var", "spack", "environments", env, "spack.lock")

    # Snapshot pre-concretize specs
    pre_specs: dict[str, Any] = {}
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                pre_data = json.load(f)
            pre_specs = pre_data.get("concrete_specs", {})
        except (json.JSONDecodeError, OSError):
            pass

    result = _run([spack, "-e", env, "concretize", "-f"], cluster=cl, timeout=300, dry_run=dry_run)
    if dry_run:
        return {"env": env, "dry_run": True, "command": result}

    # Snapshot post-concretize specs
    post_specs: dict[str, Any] = {}
    post_sha256 = ""
    if os.path.exists(lock_path):
        try:
            with open(lock_path) as f:
                data = f.read()
            post_sha256 = hashlib.sha256(data.encode()).hexdigest()
            post_data = json.loads(data)
            post_specs = post_data.get("concrete_specs", {})
        except (json.JSONDecodeError, OSError):
            pass

    pre_keys = set(pre_specs.keys())
    post_keys = set(post_specs.keys())
    added = sorted(post_keys - pre_keys)
    removed = sorted(pre_keys - post_keys)
    changed = sorted(
        k for k in (pre_keys & post_keys)
        if pre_specs.get(k) != post_specs.get(k)
    )

    return {
        "env": env,
        "added": added,
        "removed": removed,
        "changed": changed,
        "lockfile_sha256": post_sha256,
    }


def hpc_spack_env_install(env: str, *, cluster: str = "default", dry_run: bool = False) -> dict[str, Any]:
    """Install a Spack environment asynchronously.

    Returns a run_id that can be polled via hpc_job_status.
    """
    _validate(env, "environment name", _SPACK_ENV_RE)
    cl = _resolve_cluster(cluster)

    if dry_run:
        return {"dry_run": True, "command": f"{cl.spack()} -e {env} install --no-checksum=false"}

    from hpc_pilot.jobs import start_job

    log_dir = os.path.join(os.path.expanduser("~"), ".hpc-pilot", "logs", "spack", env)
    os.makedirs(log_dir, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(log_dir, f"{ts}.log")

    cmd = [cl.spack(), "-e", env, "install", "--no-checksum=false"]
    record = start_job(cmd, log_path=log_path, meta={"env": env})

    return {"run_id": record.run_id, "status": record.status, "log_path": record.log_path}


def hpc_spack_env_status(env: str, *, cluster: str = "default") -> dict[str, Any]:
    """Return parsed status of a Spack environment (installed specs)."""
    _validate(env, "environment name", _SPACK_ENV_RE)
    cl = _resolve_cluster(cluster)
    output = _run([cl.spack(), "-e", env, "spec"], cluster=cl, timeout=60)
    specs: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("==") and not stripped.startswith("Input"):
            specs.append(stripped)
    return {"env": env, "specs": specs, "spec_count": len(specs)}


def hpc_spack_install_spec(spec: str, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Install a single spec outside of an environment."""
    _validate(spec, "spec")
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "install", spec], cluster=cl, timeout=3600, dry_run=dry_run)


def hpc_spack_uninstall(spec: str, dependents: bool = False, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Uninstall a package. dry_run is mandatory."""
    _validate(spec, "spec")
    cl = _resolve_cluster(cluster)
    cmd = [cl.spack(), "uninstall"]
    if dependents:
        cmd.append("--dependents")
    cmd.append(spec)
    return _run(cmd, cluster=cl, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Mirror management
# ---------------------------------------------------------------------------


def hpc_spack_mirror_list(*, cluster: str = "default") -> str:
    """List configured Spack mirrors."""
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "mirror", "list"], cluster=cl)


def hpc_spack_mirror_add(name: str, url: str, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Add a Spack mirror."""
    _validate(name, "mirror name")
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "mirror", "add", name, url], cluster=cl, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Build cache
# ---------------------------------------------------------------------------


def hpc_spack_buildcache_push(mirror_name: str, spec: str | None = None, gpg_key: str | None = None, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Push packages to a Spack build cache."""
    _validate(mirror_name, "mirror name")
    cl = _resolve_cluster(cluster)
    cmd = [cl.spack(), "buildcache", "push"]
    if gpg_key:
        cmd.extend(["--key", gpg_key])
    cmd.append(mirror_name)
    if spec:
        cmd.append(spec)
    return _run(cmd, cluster=cl, timeout=3600, dry_run=dry_run)


def hpc_spack_buildcache_update_index(mirror_name: str, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Update the build cache index for a mirror."""
    _validate(mirror_name, "mirror name")
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "buildcache", "update-index", mirror_name], cluster=cl, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Module & compiler management
# ---------------------------------------------------------------------------


def hpc_spack_module_refresh(*, cluster: str = "default", dry_run: bool = False) -> str:
    """Refresh Spack-generated module files (lmod)."""
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "module", "lmod", "refresh", "-y"], cluster=cl, dry_run=dry_run)


def hpc_spack_compiler_find(*paths: str, cluster: str = "default", dry_run: bool = False) -> str:
    """Register new compilers with Spack."""
    cl = _resolve_cluster(cluster)
    cmd = [cl.spack(), "compiler", "find"]
    if paths:
        cmd.extend(paths)
    return _run(cmd, cluster=cl, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Spack output parsers
# ---------------------------------------------------------------------------


def parse_spack_envs(output: str) -> list[str]:
    """Parse ``spack env list`` output into a list of environment names."""
    envs: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("==>") and not stripped.startswith("#"):
            envs.append(stripped.lstrip("* "))
    return envs
