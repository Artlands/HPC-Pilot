"""Ansible tools."""
from __future__ import annotations

from hpc_pilot.tools._run import _resolve_cluster, _run


def hpc_ansible_playbook_run(
    playbook: str,
    limit: str | None = None,
    check: bool = False,
    dry_run: bool = False,
    *,
    cluster: str = "default",
) -> str:
    """Run an Ansible playbook."""
    if not playbook:
        raise ValueError("playbook path must not be empty")
    cl = _resolve_cluster(cluster)
    cmd = [cl.ansible_playbook(), playbook]
    if limit:
        cmd.extend(["--limit", limit])
    if check:
        cmd.append("--check")
    return _run(cmd, cluster=cl, timeout=600, dry_run=dry_run)


def hpc_ansible_inventory_generate(*, cluster: str = "default") -> str:
    """Return an Ansible inventory snapshot from the local inventory plugin."""
    cl = _resolve_cluster(cluster)
    return _run([cl.ansible_inventory(), "-i", "localhost,", "--list"], cluster=cl)
