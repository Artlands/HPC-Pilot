"""Canonical path helpers for HPC Pilot's home directory layout."""
from __future__ import annotations

import os

HOME_ENV = "HPC_PILOT_HOME"
_DEFAULT_HOME = "~/.hpc-pilot"
_SUBDIRS = ("skills", "skills/runs", "sessions", "logs", "jobs")


def default_home() -> str:
    """Return the compiled default home path (not affected by env overrides)."""
    return os.path.expanduser(_DEFAULT_HOME)


def get_home() -> str:
    return os.environ.get(HOME_ENV, default_home())


def config_path() -> str:
    return os.path.join(get_home(), "config.yaml")


def audit_log_path() -> str:
    return os.path.join(get_home(), "logs", "audit.jsonl")


def sessions_dir() -> str:
    return os.path.join(get_home(), "sessions")


def auth_path() -> str:
    return os.path.join(get_home(), "auth.json")


def skills_dir() -> str:
    return os.path.join(get_home(), "skills")


def skill_runs_dir() -> str:
    return os.path.join(get_home(), "skills", "runs")


def jobs_dir() -> str:
    return os.path.join(get_home(), "jobs")


def ensure_layout() -> str:
    """Create the home directory and its standard subdirectories."""
    home = get_home()
    os.makedirs(home, exist_ok=True)
    for sub in _SUBDIRS:
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    return home
