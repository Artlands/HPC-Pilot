"""Shared helpers used by CLI subcommands."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

from hpc_pilot.config import init_config  # noqa: F401 — re-exported for tests
from hpc_pilot.paths import get_home as _get_home
from hpc_pilot.rbac import Role  # noqa: F401 — used in type hints


def home_dir() -> str:
    return _get_home()


def config_file() -> str:
    return os.path.join(home_dir(), "config.yaml")


def ensure_home() -> str:
    from hpc_pilot.paths import ensure_layout
    return ensure_layout()


def _get_actor() -> str:
    return os.environ.get("HPC_PILOT_ACTOR", "cli")


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        return False
    return answer == "y"


def _make_agent(args: argparse.Namespace) -> Any:
    from hpc_pilot.agent import HpcAgent
    model: str = getattr(args, "model", None) or os.environ.get(
        "HPC_PILOT_MODEL", "claude-opus-4-7"
    )
    summarize: bool = getattr(args, "no_summarize", False) is False
    return HpcAgent(model=model, summarize=summarize)


def _resolve_cluster_flag(cluster_arg: str | None) -> str:
    if cluster_arg:
        return cluster_arg
    env_val = os.environ.get("HPC_PILOT_CLUSTER")
    if env_val:
        return env_val
    from hpc_pilot.clusters import _load_clusters
    _clusters, default_name = _load_clusters()
    return default_name


def _inject_cluster(args: argparse.Namespace, tool_args: dict[str, Any]) -> dict[str, Any]:
    if "cluster" not in tool_args:
        cluster = _resolve_cluster_flag(getattr(args, "cluster", None))
        tool_args["cluster"] = cluster
    return tool_args


def _invoke_cli(
    tool_name: str,
    tool_args: dict[str, Any],
    role: Role,
    actor: str,
    dry_run: bool = False,
    cli_args: argparse.Namespace | None = None,
) -> tuple[str | None, int]:
    from hpc_pilot.dispatch import invoke

    if cli_args is not None:
        _inject_cluster(cli_args, tool_args)

    try:
        return invoke(tool_name, tool_args, role=role, actor=actor, dry_run=dry_run), 0
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return None, 1
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return None, 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return None, 1
