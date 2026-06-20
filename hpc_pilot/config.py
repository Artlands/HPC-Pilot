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

# Gateway per-user role overrides.  Map chat/user IDs to roles.
# Any user not listed inherits the HPC_PILOT_ROLE env var or defaults to 'viewer'.
# gateway:
#   users:
#     telegram:
#       "123456789": admin
#       "987654321": operator
#     discord:
#       "111111111": operator
#
# # Per-actor rate limiting (calls per minute, default 60)
# rate_limit:
#   calls_per_minute: 60
"""


def init_config() -> None:
    """Create the default config file if it does not already exist, and
    wire up secrets manager."""
    ensure_layout()
    path = config_path()
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(DEFAULT_CONFIG)

    # Wire SecretsManager from config on every startup
    try:
        import yaml

        from hpc_pilot.secrets import configure_secrets

        if os.path.exists(path):
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
            configure_secrets(cfg)
    except Exception:
        pass  # secrets wiring must not block startup
