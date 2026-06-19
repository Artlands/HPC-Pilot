"""Configuration initialization and loading for HPC Pilot."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from hpc_pilot.paths import config_path, ensure_layout

DEFAULT_CONFIG = """\
# HPC Pilot Configuration

model:
  default: claude-opus-4-7

hpc:
  slurm_bin_dir: /usr/bin
  warewulf_bin_dir: /usr/bin
  spack_root: /opt/spack
  ansible_dir: /etc/hpc-pilot/ansible
"""


def init_config() -> None:
    """Create the default config file if it does not already exist."""
    ensure_layout()
    path = config_path()
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(DEFAULT_CONFIG)


@dataclass
class Config:
    model: str = "claude-opus-4-7"
    slurm_bin_dir: str = "/usr/bin"
    warewulf_bin_dir: str = "/usr/bin"
    spack_root: str = "/opt/spack"
    ansible_dir: str = "/etc/hpc-pilot/ansible"

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


def load_config() -> Config:
    """Load config from YAML file; fall back to defaults on any error."""
    path = config_path()
    if not os.path.exists(path):
        return Config()
    try:
        import yaml
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
        model_cfg: dict[str, Any] = data.get("model", {}) or {}
        hpc_cfg: dict[str, Any] = data.get("hpc", {}) or {}
        return Config(
            model=str(model_cfg.get("default", Config.model)),
            slurm_bin_dir=str(hpc_cfg.get("slurm_bin_dir", Config.slurm_bin_dir)),
            warewulf_bin_dir=str(hpc_cfg.get("warewulf_bin_dir", Config.warewulf_bin_dir)),
            spack_root=str(hpc_cfg.get("spack_root", Config.spack_root)),
            ansible_dir=str(hpc_cfg.get("ansible_dir", Config.ansible_dir)),
        )
    except Exception:
        return Config()
