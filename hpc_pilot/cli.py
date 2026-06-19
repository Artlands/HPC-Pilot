#!/usr/bin/env python3
"""
HPC Pilot CLI — command-line interface for HPC cluster management.

AI agent commands (require ANTHROPIC_API_KEY):
    hpc-pilot chat           Interactive AI chat session
    hpc-pilot chat -q TEXT   Single AI query, non-interactive
    hpc-pilot shell          Shell session (alias for chat with --actor/--role)

Direct cluster commands (no API key needed):
    hpc-pilot health         Check cluster health
    hpc-pilot nodes [NODE]   Show Slurm node status
    hpc-pilot queue          Show job queue
    hpc-pilot qos NAME       Inspect or modify a QOS (dry-run by default)
    hpc-pilot warewulf       Show Warewulf node status
    hpc-pilot spack          Spack environment and compiler queries
    hpc-pilot ansible        Run an Ansible playbook (dry-run by default)
    hpc-pilot version        Show version information
"""
from __future__ import annotations

import json
import os
import sys
import argparse
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Backward-compatible shims (tests patch these names in hpc_pilot.cli)
# ---------------------------------------------------------------------------
from hpc_pilot.paths import get_home as _get_home
from hpc_pilot.config import init_config  # noqa: F401 — re-exported for tests
from hpc_pilot.rbac import Role, check_permission, get_role
from hpc_pilot.audit import audit_tool


def get_hermes_home() -> str:
    return _get_home()


def get_config_path() -> str:
    return os.path.join(get_hermes_home(), "config.yaml")


def ensure_home_dir() -> str:
    from hpc_pilot.paths import ensure_layout
    return ensure_layout()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_actor() -> str:
    return os.environ.get("HPC_PILOT_ACTOR", "cli")


def _confirm(prompt: str) -> bool:
    """Prompt for y/N on stdin; returns False on EOF or non-y answer."""
    try:
        answer = input(f"{prompt} [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.", file=sys.stderr)
        return False
    return answer == "y"


def _make_agent(args: argparse.Namespace) -> "Any":
    """Build an HpcAgent from CLI args; print a helpful error if anthropic is missing."""
    from hpc_pilot.agent import HpcAgent

    model: str = getattr(args, "model", None) or os.environ.get(
        "HPC_PILOT_MODEL", "claude-opus-4-7"
    )
    return HpcAgent(model=model)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def setup_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    init_config()
    print(f"Configuration directory : {get_hermes_home()}")
    print(f"Configuration file      : {get_config_path()}")
    print()
    print("Next steps:")
    print("  1. Add your Anthropic API key to ~/.hpc-pilot/.env:")
    print("       ANTHROPIC_API_KEY=sk-ant-...")
    print("  2. (Optional) Add Telegram/Discord tokens to the same file.")
    print("  3. Run:  hpc-pilot chat")
    return 0


def chat_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    init_config()

    try:
        from hpc_pilot.agent import HpcAgent, run_chat_loop
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with: pip install 'hpc-pilot[agent]'", file=sys.stderr)
        return 1

    if not os.environ.get("ANTHROPIC_API_KEY"):
        # Try loading from .env
        from hpc_pilot.agent import _load_env
        _load_env()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set.\n"
            "Add it to ~/.hpc-pilot/.env or export it in your shell.",
            file=sys.stderr,
        )
        return 1

    agent = _make_agent(args)

    query: str | None = getattr(args, "query", None)
    if query:
        # Single-shot non-interactive mode
        try:
            text = agent.run_query(query)
            print(text)
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    return run_chat_loop(agent)


def shell_command(args: argparse.Namespace) -> int:
    # Wire --role and --actor into env before constructing the agent
    role_arg = getattr(args, "role", None)
    if role_arg:
        os.environ["HPC_PILOT_ROLE"] = role_arg
    actor_arg = getattr(args, "actor", None)
    if actor_arg:
        os.environ["HPC_PILOT_ACTOR"] = actor_arg

    # Re-use chat_command logic (shell is an alias with extra flags)
    return chat_command(args)


def cron_command(args: argparse.Namespace) -> int:
    print(
        "hpc-pilot cron: scheduled monitoring is planned but not yet implemented.",
        file=sys.stderr,
    )
    return 1


def tui_command(args: argparse.Namespace) -> int:
    print(
        "hpc-pilot tui: text-based UI is planned but not yet implemented.",
        file=sys.stderr,
    )
    return 1


def gateway_command(args: argparse.Namespace) -> int:
    if getattr(args, "start", False) or not any(
        [getattr(args, "stop", False), getattr(args, "status", False), getattr(args, "setup", False)]
    ):
        return _nyi("gateway --start")
    if getattr(args, "setup", False):
        print("Gateway setup:")
        print("  1. Add API keys to ~/.hpc-pilot/.env")
        print("  2. Edit ~/.hpc-pilot/config.yaml for platform settings")
        print("  3. (Agent layer not yet implemented)")
        return 0
    if getattr(args, "status", False):
        print("Gateway: not running (agent layer not yet implemented)")
        return 0
    if getattr(args, "stop", False):
        print("Gateway: not running")
        return 0
    return 0


def health_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    role = get_role()
    try:
        check_permission("hpc_cluster_health_check", role)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    from hpc_pilot.tools import hpc_cluster_health_check
    actor = _get_actor()
    try:
        with audit_tool("hpc_cluster_health_check", actor, role.value, {}, dry_run=False):
            result = hpc_cluster_health_check()
        print(json.dumps(result, indent=2, default=str))
        return 0
    except Exception as exc:
        print(f"Health check error: {exc}", file=sys.stderr)
        return 1


def nodes_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    role = get_role()
    try:
        check_permission("hpc_slurm_node_status", role)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    from hpc_pilot.tools import hpc_slurm_node_status
    node: str = getattr(args, "node", "") or ""
    actor = _get_actor()
    try:
        with audit_tool("hpc_slurm_node_status", actor, role.value, {"node": node}, dry_run=False):
            result = hpc_slurm_node_status(node)
        print(result)
        return 0
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def queue_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    role = get_role()
    try:
        check_permission("hpc_slurm_queue", role)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    from hpc_pilot.tools import hpc_slurm_queue
    filters: dict[str, str] = {}
    if getattr(args, "user", None):
        filters["user"] = args.user
    if getattr(args, "partition", None):
        filters["partition"] = args.partition

    actor = _get_actor()
    try:
        with audit_tool("hpc_slurm_queue", actor, role.value, {"filters": filters}, dry_run=False):
            result = hpc_slurm_queue(filters or None)
        print(result)
        return 0
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def qos_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    role = get_role()
    try:
        check_permission("hpc_slurm_qos_modify", role)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    from hpc_pilot.tools import hpc_slurm_qos_modify
    apply_flag: bool = getattr(args, "apply", False)
    yes_flag: bool = getattr(args, "yes", False)
    dry_run = not apply_flag
    max_wall: int | None = getattr(args, "max_wall_min", None)
    actor = _get_actor()

    try:
        if dry_run:
            result = hpc_slurm_qos_modify(args.name, max_wall, dry_run=True)
            print(result)
            print("\nUse --apply to execute. Add --yes to skip confirmation.")
            return 0

        if not yes_flag and not _confirm(f"Modify QOS '{args.name}'?"):
            print("Aborted.")
            return 0

        audit_args = {"name": args.name, "max_wall_min": max_wall}
        with audit_tool("hpc_slurm_qos_modify", actor, role.value, audit_args, dry_run=False):
            result = hpc_slurm_qos_modify(args.name, max_wall, dry_run=False)
        print(result)
        return 0
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def warewulf_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    role = get_role()
    try:
        check_permission("hpc_warewulf_node_status", role)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    from hpc_pilot.tools import hpc_warewulf_node_status
    actor = _get_actor()
    try:
        with audit_tool("hpc_warewulf_node_status", actor, role.value, {}, dry_run=False):
            result = hpc_warewulf_node_status()
        print(result)
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def spack_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    role = get_role()

    from hpc_pilot.tools import hpc_spack_env_list, hpc_spack_find, hpc_spack_compilers
    actor = _get_actor()
    action: str = getattr(args, "action", "list") or "list"

    tool_name = "hpc_spack_env_list"
    try:
        check_permission(tool_name, role)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    try:
        if action == "find":
            with audit_tool("hpc_spack_find", actor, role.value, {"env": args.env}, dry_run=False):
                result = hpc_spack_find(args.env)
        elif action == "compilers":
            with audit_tool("hpc_spack_compilers", actor, role.value, {}, dry_run=False):
                result = hpc_spack_compilers()
        else:
            with audit_tool("hpc_spack_env_list", actor, role.value, {}, dry_run=False):
                result = hpc_spack_env_list()
        print(result)
        return 0
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def ansible_command(args: argparse.Namespace) -> int:
    ensure_home_dir()
    role = get_role()
    try:
        check_permission("hpc_ansible_playbook_run", role)
    except PermissionError as exc:
        print(f"Permission denied: {exc}", file=sys.stderr)
        return 1

    from hpc_pilot.tools import hpc_ansible_playbook_run
    apply_flag: bool = getattr(args, "apply", False)
    yes_flag: bool = getattr(args, "yes", False)
    check_flag: bool = getattr(args, "check", False)
    dry_run = not apply_flag
    limit: str | None = getattr(args, "limit", None)
    actor = _get_actor()

    try:
        if dry_run:
            result = hpc_ansible_playbook_run(args.playbook, limit, check_flag, dry_run=True)
            print(result)
            print("\nUse --apply to execute. Add --yes to skip confirmation.")
            return 0

        if not yes_flag and not _confirm(f"Run playbook '{args.playbook}'?"):
            print("Aborted.")
            return 0

        audit_args = {"playbook": args.playbook, "limit": limit, "check": check_flag}
        with audit_tool("hpc_ansible_playbook_run", actor, role.value, audit_args, dry_run=False):
            result = hpc_ansible_playbook_run(args.playbook, limit, check_flag, dry_run=False)
        print(result)
        return 0
    except ValueError as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def version_command(args: argparse.Namespace) -> int:
    from hpc_pilot import __version__
    try:
        print(f"HPC Pilot {__version__}")
        print(f"Python: {'.'.join(map(str, sys.version_info[:3]))}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hpc-pilot",
        description="HPC Pilot — AI agent for HPC cluster management",
    )
    subs = parser.add_subparsers(dest="command", help="Available commands")

    # chat (NYI)
    chat_p = subs.add_parser("chat", help="[planned] Start interactive AI chat")
    chat_p.add_argument("-q", "--query", help="Single query")
    chat_p.add_argument("-m", "--model", help="Model name")
    chat_p.set_defaults(func=chat_command)

    # shell (NYI)
    shell_p = subs.add_parser("shell", help="[planned] Start shell session")
    shell_p.add_argument("--actor", default="cli-user", help="Operator identity")
    shell_p.add_argument("--role", default="operator", help="RBAC role")
    shell_p.set_defaults(func=shell_command)

    # tui (NYI)
    tui_p = subs.add_parser("tui", help="[planned] Start text-based UI")
    tui_p.set_defaults(func=tui_command)

    # gateway (NYI for --start)
    gw_p = subs.add_parser("gateway", help="[planned] Gateway service control")
    gw_p.add_argument("--start", action="store_true")
    gw_p.add_argument("--stop", action="store_true")
    gw_p.add_argument("--status", action="store_true")
    gw_p.add_argument("--setup", action="store_true")
    gw_p.add_argument("--port", type=int, default=8000)
    gw_p.add_argument("--host", default="127.0.0.1")
    gw_p.set_defaults(func=gateway_command)

    # setup (NYI)
    setup_p = subs.add_parser("setup", help="[planned] Configuration wizard")
    setup_p.set_defaults(func=setup_command)

    # health
    health_p = subs.add_parser("health", help="Check cluster health")
    health_p.set_defaults(func=health_command)

    # nodes
    nodes_p = subs.add_parser("nodes", help="Show Slurm node status")
    nodes_p.add_argument("node", nargs="?", default="", help="Node name (omit for all)")
    nodes_p.set_defaults(func=nodes_command)

    # queue
    queue_p = subs.add_parser("queue", help="Show job queue")
    queue_p.add_argument("--user", help="Filter by user")
    queue_p.add_argument("--partition", help="Filter by partition")
    queue_p.set_defaults(func=queue_command)

    # qos
    qos_p = subs.add_parser("qos", help="Inspect or modify a Slurm QOS")
    qos_p.add_argument("name", help="QOS name")
    qos_p.add_argument("--max-wall-min", type=int, dest="max_wall_min", help="Max wall time (min)")
    qos_p.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    qos_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    qos_p.set_defaults(func=qos_command)

    # warewulf
    ww_p = subs.add_parser("warewulf", help="Show Warewulf node status")
    ww_p.set_defaults(func=warewulf_command)

    # spack
    spack_p = subs.add_parser("spack", help="Spack queries")
    spack_subs = spack_p.add_subparsers(dest="action")
    spack_subs.add_parser("list", help="List environments")
    spack_find_p = spack_subs.add_parser("find", help="List specs in an environment")
    spack_find_p.add_argument("env", help="Environment name")
    spack_subs.add_parser("compilers", help="List compilers")
    spack_p.set_defaults(func=spack_command)

    # ansible
    ansible_p = subs.add_parser("ansible", help="Run an Ansible playbook")
    ansible_p.add_argument("playbook", help="Path to playbook YAML")
    ansible_p.add_argument("--limit", help="Host limit")
    ansible_p.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    ansible_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    ansible_p.add_argument("--check", action="store_true", help="Ansible check mode")
    ansible_p.set_defaults(func=ansible_command)

    # cron (NYI)
    cron_p = subs.add_parser("cron", help="[planned] Scheduled cluster monitoring")
    cron_p.set_defaults(func=cron_command)

    # version
    version_p = subs.add_parser("version", help="Show version")
    version_p.set_defaults(func=version_command)

    args = parser.parse_args(argv)

    if not args.command:
        args.func = chat_command  # type: ignore[attr-defined]

    if hasattr(args, "func"):
        return args.func(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
