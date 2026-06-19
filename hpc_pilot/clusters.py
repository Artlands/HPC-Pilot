"""Multi-cluster context abstraction for HPC Pilot."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SSHConfig:
    host: str
    user: str
    key: str
    control_path: str = ""


@dataclass(frozen=True)
class Cluster:
    name: str
    slurm_bin_dir: str = "/usr/bin"
    warewulf_bin_dir: str = "/usr/bin"
    spack_root: str = "/opt/spack"
    ansible_dir: str = "/etc/hpc-pilot/ansible"
    ssh: SSHConfig | None = None

    def slurm(self, binary: str) -> str:
        return os.path.join(self.slurm_bin_dir, binary)

    def warewulf(self, binary: str) -> str:
        return os.path.join(self.warewulf_bin_dir, binary)

    def spack(self) -> str:
        return os.path.join(self.spack_root, "bin", "spack")

    def ansible_playbook(self) -> str:
        return "ansible-playbook"

    def ansible_inventory(self) -> str:
        return "ansible-inventory"


def _parse_cluster(name: str, data: dict[str, Any]) -> Cluster:
    ssh_cfg: SSHConfig | None = None
    ssh_data = data.get("ssh")
    if ssh_data and isinstance(ssh_data, dict):
        ssh_cfg = SSHConfig(
            host=str(ssh_data.get("host", "")),
            user=str(ssh_data.get("user", "")),
            key=str(ssh_data.get("key", "")),
            control_path=str(ssh_data.get("control_path", "")),
        )
    return Cluster(
        name=name,
        slurm_bin_dir=str(data.get("slurm_bin_dir", "/usr/bin")),
        warewulf_bin_dir=str(data.get("warewulf_bin_dir", "/usr/bin")),
        spack_root=str(data.get("spack_root", "/opt/spack")),
        ansible_dir=str(data.get("ansible_dir", "/etc/hpc-pilot/ansible")),
        ssh=ssh_cfg,
    )


def _load_clusters() -> tuple[dict[str, Cluster], str]:
    """Return (clusters_dict, default_cluster_name) from config.yaml."""
    from hpc_pilot.paths import config_path

    path = config_path()
    if not os.path.exists(path):
        return {"default": Cluster(name="default")}, "default"

    try:
        import yaml

        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}

        default_name: str = str(data.get("default_cluster", "default"))

        clusters_raw = data.get("clusters")
        if clusters_raw and isinstance(clusters_raw, dict):
            clusters = {
                name: _parse_cluster(name, cfg or {})
                for name, cfg in clusters_raw.items()
            }
            if default_name not in clusters:
                # Fall back to a bare default if missing
                clusters[default_name] = Cluster(name=default_name)
            return clusters, default_name

        # Backward compat: old "hpc:" section maps to the default cluster
        hpc_cfg: dict[str, Any] = data.get("hpc", {}) or {}
        default_cluster = Cluster(
            name=default_name,
            slurm_bin_dir=str(hpc_cfg.get("slurm_bin_dir", "/usr/bin")),
            warewulf_bin_dir=str(hpc_cfg.get("warewulf_bin_dir", "/usr/bin")),
            spack_root=str(hpc_cfg.get("spack_root", "/opt/spack")),
            ansible_dir=str(hpc_cfg.get("ansible_dir", "/etc/hpc-pilot/ansible")),
        )
        return {default_name: default_cluster}, default_name

    except Exception:
        return {"default": Cluster(name="default")}, "default"


def get_cluster(name: str | None = None) -> Cluster:
    """Return the Cluster config for *name* (or the default cluster when None).

    Raises ValueError for unknown cluster names.
    """
    clusters, default_name = _load_clusters()
    resolved = name or default_name
    if resolved not in clusters:
        raise ValueError(
            f"Unknown cluster: {resolved!r}. Available: {sorted(clusters)}"
        )
    return clusters[resolved]


def list_clusters() -> list[str]:
    """Return a sorted list of configured cluster names."""
    clusters, _ = _load_clusters()
    return sorted(clusters)
