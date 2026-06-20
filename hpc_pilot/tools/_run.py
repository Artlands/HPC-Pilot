"""Subprocess runner and cluster-availability probes."""

from __future__ import annotations

import os
import shlex
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hpc_pilot.clusters import Cluster


def _run(
    cmd: list[str],
    *,
    cluster: Cluster | None = None,
    timeout: int = 60,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> str:
    """Run *cmd* and return stdout; raise RuntimeError on non-zero exit.

    When *cluster* has SSH config the command is wrapped in an SSH call.
    When dry_run is True, return the shell-quoted command without executing.
    """
    if dry_run:
        return "DRY-RUN: " + " ".join(shlex.quote(c) for c in cmd)

    actual_cmd: list[str] = cmd
    if cluster is not None and cluster.ssh is not None:
        ssh = cluster.ssh
        actual_cmd = [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
        ]
        # StrictHostKeyChecking policy
        if ssh.host_key_check:
            actual_cmd += ["-o", f"StrictHostKeyChecking={ssh.host_key_check}"]
        if ssh.known_hosts:
            actual_cmd += ["-o", f"UserKnownHostsFile={ssh.known_hosts}"]
        actual_cmd += [
            "-i",
            os.path.expanduser(ssh.key),
            f"{ssh.user}@{ssh.host}",
            "--",
            *map(shlex.quote, cmd),
        ]
        timeout += 5
        if ssh.control_path:
            actual_cmd[1:1] = ["-o", f"ControlPath={ssh.control_path}", "-o", "ControlMaster=auto"]

    result = subprocess.run(actual_cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"{cmd[0]} exited {result.returncode}: {stderr or '(no stderr)'}")
    return result.stdout


def _resolve_cluster(name: str) -> Cluster:
    from hpc_pilot.clusters import get_cluster

    return get_cluster(name)


# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


def check_slurm_available(cluster: Cluster | None = None) -> bool:
    try:
        if cluster is not None:
            binary = cluster.slurm("scontrol")
        else:
            from hpc_pilot.clusters import get_cluster

            binary = get_cluster("default").slurm("scontrol")
        subprocess.run([binary, "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_warewulf_available(cluster: Cluster | None = None) -> bool:
    try:
        if cluster is not None:
            binary = cluster.warewulf("wwctl")
        else:
            from hpc_pilot.clusters import get_cluster

            binary = get_cluster("default").warewulf("wwctl")
        subprocess.run([binary, "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_spack_available(cluster: Cluster | None = None) -> bool:
    try:
        if cluster is not None:
            binary = cluster.spack()
        else:
            from hpc_pilot.clusters import get_cluster

            binary = get_cluster("default").spack()
        subprocess.run([binary, "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_ansible_available() -> bool:
    try:
        subprocess.run(["ansible", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False
