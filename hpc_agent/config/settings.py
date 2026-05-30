"""Global settings, env-overridable. See spec 00 §7."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HPC_", env_file=".env", extra="ignore")

    db_url: str = "postgresql+psycopg://hpcagent@localhost/hpc_agent"
    audit_db_url: str = "postgresql+psycopg://hpcagent@localhost/hpc_audit"
    audit_sink: str = "memory"  # memory | db
    audit_auto_init: bool = False

    config_repo: str = "/etc/hpc-agent/config"
    slurm_bin_dir: str = "/usr/bin"
    ww_bin_dir: str = "/usr/bin"
    spack_root: str = "/opt/spack"
    ansible_dir: str = "/etc/hpc-agent/ansible"

    approval_backend: str = "cli"  # cli | slack | api
    dry_run_default: bool = True
    max_blast_radius_auto: int = 4
    approval_ttl_s: int = 3600


settings = Settings()
