"""Spack tools and output parsers."""
from __future__ import annotations

from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _USER_RE, _validate


def hpc_spack_env_list(*, cluster: str = "default") -> str:
    """Return spack env list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "env", "list"], cluster=cl)


def hpc_spack_find(env: str, *, cluster: str = "default") -> str:
    """Return installed specs in a Spack environment."""
    _validate(env, "environment name", _USER_RE)
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "find", "-l", "-N", "-d", "-e", env], cluster=cl, timeout=60)


def hpc_spack_compilers(*, cluster: str = "default") -> str:
    """Return the list of available Spack compilers."""
    cl = _resolve_cluster(cluster)
    return _run([cl.spack(), "compilers"], cluster=cl)


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
