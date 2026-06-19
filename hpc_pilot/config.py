"""Configuration initialization for HPC Pilot."""
from __future__ import annotations

import os

from hpc_pilot.paths import config_path, ensure_layout

DEFAULT_CONFIG = """\
# HPC Pilot Configuration

model:
  default: claude-opus-4-7

# Multi-cluster configuration.  Add more named clusters as needed.
clusters:
  default:
    slurm_bin_dir: /usr/bin
    warewulf_bin_dir: /usr/bin
    spack_root: /opt/spack
    ansible_dir: /etc/hpc-pilot/ansible
    # Optional SSH config when the Slurm controller is remote:
    # ssh:
    #   host: head01.example.com
    #   user: hpcadmin
    #   key: ~/.ssh/hpc-pilot

default_cluster: default
"""


def init_config() -> None:
    """Create the default config file if it does not already exist."""
    ensure_layout()
    path = config_path()
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(DEFAULT_CONFIG)
