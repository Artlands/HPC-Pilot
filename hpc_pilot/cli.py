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

import argparse
import contextlib
import json
import os
import signal
import sys
import time
import warnings
from collections.abc import Callable
from typing import Any

from hpc_pilot.config import init_config  # noqa: F401 — re-exported for tests
from hpc_pilot.paths import get_home as _get_home
from hpc_pilot.rbac import Role, get_role


def home_dir() -> str:
    return _get_home()


def config_file() -> str:
    return os.path.join(home_dir(), "config.yaml")


def ensure_home() -> str:
    from hpc_pilot.paths import ensure_layout
    return ensure_layout()


# ---------------------------------------------------------------------------
# Deprecated shims — kept for backward compatibility
# ---------------------------------------------------------------------------

def get_hermes_home() -> str:
    warnings.warn("get_hermes_home() is deprecated; use home_dir()", DeprecationWarning, stacklevel=2)
    return home_dir()


def get_config_path() -> str:
    warnings.warn("get_config_path() is deprecated; use config_file()", DeprecationWarning, stacklevel=2)
    return config_file()


def ensure_home_dir() -> str:
    warnings.warn("ensure_home_dir() is deprecated; use ensure_home()", DeprecationWarning, stacklevel=2)
    return ensure_home()


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


def _make_agent(args: argparse.Namespace) -> Any:
    """Build an HpcAgent from CLI args; print a helpful error if anthropic is missing."""
    from hpc_pilot.agent import HpcAgent

    model: str = getattr(args, "model", None) or os.environ.get(
        "HPC_PILOT_MODEL", "claude-opus-4-7"
    )
    summarize: bool = getattr(args, "no_summarize", False) is False
    return HpcAgent(model=model, summarize=summarize)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


def setup_command(args: argparse.Namespace) -> int:
    ensure_home()
    init_config()
    print(f"Configuration directory : {home_dir()}")
    print(f"Configuration file      : {config_file()}")
    print()
    print("Next steps:")
    print("  1. Add your Anthropic API key to ~/.hpc-pilot/.env:")
    print("       ANTHROPIC_API_KEY=sk-ant-...")
    print("  2. (Optional) Add Telegram/Discord tokens to the same file.")
    print("  3. Run:  hpc-pilot chat")
    return 0


def chat_command(args: argparse.Namespace) -> int:
    ensure_home()
    init_config()

    # --list-sessions doesn't require the API key
    if getattr(args, "list_sessions", False):
        import datetime

        from hpc_pilot.agent import list_sessions
        sessions = list_sessions()
        if not sessions:
            print("No saved sessions.")
        else:
            for s in sessions:
                ts = datetime.datetime.fromtimestamp(s["ts"]).strftime("%Y-%m-%d %H:%M:%S")
                print(f"{s['id']}  {ts}  {s['turn_count']} turn(s)  [{s['role']}]")
        return 0

    try:
        from hpc_pilot.agent import load_session, run_chat_loop
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with: pip install 'hpc-pilot[agent]'", file=sys.stderr)
        return 1

    # Validate --resume before touching the API key — gives a clear error immediately.
    initial_history: list[dict[str, Any]] | None = None
    resume_id: str | None = getattr(args, "resume", None)
    if resume_id:
        try:
            initial_history, _meta = load_session(resume_id)
            turn_count = sum(1 for m in initial_history if m.get("role") == "user")
            print(f"Resuming session {resume_id}  ({turn_count} previous turn(s))\n")
        except FileNotFoundError:
            print(f"Session not found: {resume_id}", file=sys.stderr)
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

    return run_chat_loop(agent, initial_history=initial_history)


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
    """Gateway daemon control: start, stop, status."""
    from hpc_pilot.paths import gateway_pid_path

    pid_path = gateway_pid_path()

    if getattr(args, "start", False):
        return _gateway_start(args, pid_path)

    if getattr(args, "stop", False):
        return _gateway_stop(pid_path)

    if getattr(args, "status", False):
        return _gateway_status(pid_path)

    if getattr(args, "setup", False):
        from hpc_pilot.gateway import main as gateway_main
        return gateway_main(["--setup"])

    # Default: start
    return _gateway_start(args, pid_path)


def webui_command(args: argparse.Namespace) -> int:
    """Launch the HPC Pilot Web UI (FastAPI)."""
    try:
        from hpc_pilot.webui import run_webui
    except ImportError as exc:
        print(f"Missing dependency: {exc}", file=sys.stderr)
        print("Install with: pip install 'hpc-pilot[webui]'", file=sys.stderr)
        return 1

    port: int = getattr(args, "port", 0) or int(os.environ.get("HPC_PILOT_PORT", "8000"))
    host: str = getattr(args, "host", "127.0.0.1")
    run_webui(host=host, port=port)
    return 0


def _gateway_start(args: argparse.Namespace, pid_path: str) -> int:
    """Start the gateway daemon. Writes PID file and runs the gateway loop."""
    from hpc_pilot.gateway import main as gateway_main

    # Check if already running
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            print(f"Gateway is already running (PID {old_pid}).", file=sys.stderr)
            return 1
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                os.remove(pid_path)

    # Write PID file
    pid = os.getpid()
    os.makedirs(os.path.dirname(pid_path), exist_ok=True)
    with open(pid_path, "w") as f:
        f.write(str(pid))

    def _cleanup(signum: Any | None = None, frame: Any | None = None) -> None:
        try:
            if os.path.exists(pid_path):
                os.remove(pid_path)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _cleanup)
    signal.signal(signal.SIGINT, _cleanup)

    try:
        return gateway_main(["--start"])
    finally:
        _cleanup()


def _gateway_stop(pid_path: str) -> int:
    """Stop the gateway daemon by sending SIGTERM to the PID file process."""
    if not os.path.exists(pid_path):
        print("Gateway is not running (no PID file found).", file=sys.stderr)
        return 1

    try:
        with open(pid_path) as f:
            pid = int(f.read().strip())
    except (ValueError, OSError) as exc:
        print(f"Invalid PID file: {exc}", file=sys.stderr)
        return 1

    try:
        os.kill(pid, signal.SIGTERM)
    except PermissionError:
        print(f"Permission denied: cannot send SIGTERM to PID {pid}.", file=sys.stderr)
        return 1
    except ProcessLookupError:
        print(f"Gateway process (PID {pid}) not found; removing stale PID file.")
        with contextlib.suppress(OSError):
            os.remove(pid_path)
        return 1

    # Wait up to 10s for graceful shutdown
    for _ in range(100):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(pid_path)
            print("Gateway stopped.")
            return 0

    # Escalate to SIGKILL
    print("Gateway did not stop gracefully; sending SIGKILL.", file=sys.stderr)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
        with contextlib.suppress(OSError):
            os.remove(pid_path)
    print("Gateway killed.")
    return 0


def _gateway_status(pid_path: str) -> int:
    """Check if the gateway daemon is running."""
    if not os.path.exists(pid_path):
        print("Gateway: NOT RUNNING")
        return 1

    try:
        with open(pid_path) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        print(f"Gateway: RUNNING (PID {pid})")
        return 0
    except (OSError, ValueError):
        print("Gateway: NOT RUNNING (stale PID file)")
        return 1


# ---------------------------------------------------------------------------
# Approve subcommand
# ---------------------------------------------------------------------------


def approve_command(args: argparse.Namespace) -> int:
    """Approve or reject out-of-band approval requests."""
    from hpc_pilot.approvals import approve_request, list_pending, reject_request

    ensure_home()

    if getattr(args, "list_approvals", False):
        pending = list_pending()
        if not pending:
            print("No pending approvals.")
        else:
            print(f"{'ID':<36} {'Tool':<30} {'Requester':<20} {'Risk Summary'}")
            print("-" * 110)
            for req in pending:
                print(f"{req.id:<36} {req.tool:<30} {req.requester_actor:<20} {req.risk_summary}")
        return 0

    request_id = getattr(args, "request_id", None)
    if not request_id:
        print("approve: an approval ID is required (use --list to see pending).", file=sys.stderr)
        print("Usage: hpc-pilot approve <approval-id> [--reject]", file=sys.stderr)
        return 2

    reject = getattr(args, "reject", False)
    actor = _get_actor()

    try:
        if reject:
            req = reject_request(request_id, actor)
            print(f"Approval {request_id} rejected by {actor}.")
        else:
            req = approve_request(request_id, actor)
            print(f"Approval {request_id} approved by {actor}.")
            print(f"  Tool: {req.tool}")
            print(f"  Cluster: {req.cluster}")
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


def _resolve_cluster_flag(cluster_arg: str | None) -> str:
    """Resolve the --cluster flag from CLI arg, env var, or fall back to 'default'."""
    if cluster_arg:
        return cluster_arg
    env_val = os.environ.get("HPC_PILOT_CLUSTER")
    if env_val:
        return env_val
    # Try config file for default_cluster
    from hpc_pilot.clusters import _load_clusters
    _clusters, default_name = _load_clusters()
    return default_name


def _inject_cluster(args: argparse.Namespace, tool_args: dict[str, Any]) -> dict[str, Any]:
    """Add the cluster key to tool_args if not already present."""
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
    """Run a tool via dispatch.invoke; return (result_text, exit_code)."""
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


def health_command(args: argparse.Namespace) -> int:
    ensure_home()
    result, code = _invoke_cli("hpc_cluster_health_check", {}, get_role(), _get_actor(), cli_args=args)
    if result is not None:
        print(result)
    return code


def nodes_command(args: argparse.Namespace) -> int:
    ensure_home()
    node: str = getattr(args, "node", "") or ""
    result, code = _invoke_cli(
        "hpc_slurm_node_status", {"node": node}, get_role(), _get_actor(),
        cli_args=args,
    )
    if result is not None:
        if getattr(args, "json", False):
            from hpc_pilot.tools.slurm import parse_slurm_nodes
            print(json.dumps(parse_slurm_nodes(result), indent=2))
        else:
            print(result)
    return code


def queue_command(args: argparse.Namespace) -> int:
    ensure_home()
    tool_args: dict[str, Any] = {}
    if getattr(args, "user", None):
        tool_args["user"] = args.user
    if getattr(args, "partition", None):
        tool_args["partition"] = args.partition
    result, code = _invoke_cli("hpc_slurm_queue", tool_args, get_role(), _get_actor(), cli_args=args)
    if result is not None:
        if getattr(args, "json", False):
            from hpc_pilot.tools.slurm import parse_slurm_queue
            print(json.dumps(parse_slurm_queue(result), indent=2))
        else:
            print(result)
    return code


def qos_command(args: argparse.Namespace) -> int:
    ensure_home()
    role = get_role()
    apply_flag: bool = getattr(args, "apply", False)
    yes_flag: bool = getattr(args, "yes", False)
    dry_run = not apply_flag
    max_wall: int | None = getattr(args, "max_wall_min", None)
    tool_args: dict[str, Any] = {"name": args.name, "max_wall_min": max_wall, "dry_run": dry_run}

    if dry_run:
        result, code = _invoke_cli("hpc_slurm_qos_modify", tool_args, role, _get_actor(), dry_run=True, cli_args=args)
        if result is not None:
            print(result)
            print("\nUse --apply to execute. Add --yes to skip confirmation.")
        return code

    if not yes_flag and not _confirm(f"Modify QOS '{args.name}'?"):
        print("Aborted.")
        return 0

    tool_args["dry_run"] = False
    result, code = _invoke_cli("hpc_slurm_qos_modify", tool_args, role, _get_actor(), dry_run=False, cli_args=args)
    if result is not None:
        print(result)
    return code


def warewulf_command(args: argparse.Namespace) -> int:
    ensure_home()
    result, code = _invoke_cli("hpc_warewulf_node_status", {}, get_role(), _get_actor(), cli_args=args)
    if result is not None:
        if getattr(args, "json", False):
            from hpc_pilot.tools.warewulf import parse_warewulf_nodes
            print(json.dumps(parse_warewulf_nodes(result), indent=2))
        else:
            print(result)
    return code


def spack_command(args: argparse.Namespace) -> int:
    ensure_home()
    role = get_role()
    actor = _get_actor()
    action: str = getattr(args, "action", "list") or "list"
    emit_json: bool = getattr(args, "json", False)

    if action == "find":
        result, code = _invoke_cli("hpc_spack_find", {"env": args.env}, role, actor, cli_args=args)
    elif action == "compilers":
        result, code = _invoke_cli("hpc_spack_compilers", {}, role, actor, cli_args=args)
    else:
        result, code = _invoke_cli("hpc_spack_env_list", {}, role, actor, cli_args=args)

    if result is not None:
        if emit_json and action not in ("find", "compilers"):
            from hpc_pilot.tools.spack import parse_spack_envs
            print(json.dumps(parse_spack_envs(result), indent=2))
        else:
            print(result)
    return code


def ansible_command(args: argparse.Namespace) -> int:
    ensure_home()
    role = get_role()
    apply_flag: bool = getattr(args, "apply", False)
    yes_flag: bool = getattr(args, "yes", False)
    check_flag: bool = getattr(args, "check", False)
    dry_run = not apply_flag
    limit: str | None = getattr(args, "limit", None)
    tool_args: dict[str, Any] = {
        "playbook": args.playbook, "limit": limit, "check": check_flag, "dry_run": dry_run,
    }

    if dry_run:
        result, code = _invoke_cli("hpc_ansible_playbook_run", tool_args, role, _get_actor(), dry_run=True, cli_args=args)
        if result is not None:
            print(result)
            print("\nUse --apply to execute. Add --yes to skip confirmation.")
        return code

    if not yes_flag and not _confirm(f"Run playbook '{args.playbook}'?"):
        print("Aborted.")
        return 0

    tool_args["dry_run"] = False
    result, code = _invoke_cli("hpc_ansible_playbook_run", tool_args, role, _get_actor(), dry_run=False, cli_args=args)
    if result is not None:
        print(result)
    return code


def reservation_command(args: argparse.Namespace) -> int:
    ensure_home()
    role = get_role()
    actor = _get_actor()
    action: str = getattr(args, "action", "list") or "list"

    if action == "list":
        result, code = _invoke_cli("hpc_slurm_reservation_list", {}, role, actor, cli_args=args)
    elif action == "create":
        tool_args: dict[str, Any] = {
            "name": args.name,
            "nodes": args.nodes,
            "start": args.start,
            "duration": args.duration,
            "users": getattr(args, "users", "") or "",
            "accounts": getattr(args, "accounts", "") or "",
            "flags": getattr(args, "flags", "") or "",
            "dry_run": not getattr(args, "apply", False),
        }
        result, code = _invoke_cli(
            "hpc_slurm_reservation_create", tool_args, role, actor,
            dry_run=tool_args["dry_run"],
            cli_args=args,
        )
        if result and tool_args["dry_run"]:
            print(result)
            print("\nUse --apply to execute.")
            return code
    elif action == "update":
        tool_args = {
            "name": args.name,
            "nodes": getattr(args, "nodes", "") or "",
            "start": getattr(args, "start", "") or "",
            "duration": getattr(args, "duration", "") or "",
            "users": getattr(args, "users", "") or "",
            "flags": getattr(args, "flags", "") or "",
            "dry_run": not getattr(args, "apply", False),
        }
        result, code = _invoke_cli(
            "hpc_slurm_reservation_update", tool_args, role, actor,
            dry_run=tool_args["dry_run"],
            cli_args=args,
        )
        if result and tool_args["dry_run"]:
            print(result)
            print("\nUse --apply to execute.")
            return code
    elif action == "delete":
        dry_run = not getattr(args, "apply", False)
        yes_flag = getattr(args, "yes", False)
        if not dry_run and not yes_flag and not _confirm(f"Delete reservation '{args.name}'?"):
            print("Aborted.")
            return 0
        result, code = _invoke_cli(
            "hpc_slurm_reservation_delete",
            {"name": args.name, "dry_run": dry_run},
            role, actor, dry_run=dry_run,
            cli_args=args,
        )
        if result and dry_run:
            print(result)
            print("\nUse --apply to execute.")
            return code
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        return 1

    if result is not None:
        print(result)
    return code


def account_command(args: argparse.Namespace) -> int:
    ensure_home()
    role = get_role()
    actor = _get_actor()
    action: str = getattr(args, "action", "list") or "list"

    if action == "list":
        result, code = _invoke_cli("hpc_slurm_account_list", {}, role, actor, cli_args=args)
    elif action == "create":
        dry_run = not getattr(args, "apply", False)
        yes_flag = getattr(args, "yes", False)
        tool_args = {
            "name": args.name,
            "description": getattr(args, "description", "") or "",
            "organization": getattr(args, "organization", "") or "",
            "dry_run": dry_run,
        }
        if dry_run:
            result, code = _invoke_cli(
                "hpc_slurm_account_create", tool_args, role, actor, dry_run=True,
                cli_args=args,
            )
            if result:
                print(result)
                print("\nUse --apply to execute.")
            return code
        if not yes_flag and not _confirm(f"Create account '{args.name}'?"):
            print("Aborted.")
            return 0
        tool_args["dry_run"] = False
        result, code = _invoke_cli("hpc_slurm_account_create", tool_args, role, actor, cli_args=args)
    else:
        print(f"Unknown action: {action}", file=sys.stderr)
        return 1

    if result is not None:
        print(result)
    return code


def accounting_command(args: argparse.Namespace) -> int:
    ensure_home()
    tool_args: dict[str, Any] = {
        "user": getattr(args, "user", "") or "",
        "account": getattr(args, "account", "") or "",
        "start": getattr(args, "start", "") or "",
        "end": getattr(args, "end", "") or "",
        "state": getattr(args, "state", "") or "",
    }
    result, code = _invoke_cli("hpc_slurm_accounting", tool_args, get_role(), _get_actor(), cli_args=args)
    if result is not None:
        if getattr(args, "json", False):
            from hpc_pilot.tools.slurm_parsers import parse_sacct
            print(json.dumps(parse_sacct(result), indent=2))
        else:
            print(result)
    return code


def sdiag_command(args: argparse.Namespace) -> int:
    ensure_home()
    result, code = _invoke_cli("hpc_slurm_sdiag", {}, get_role(), _get_actor(), cli_args=args)
    if result is not None:
        print(result)
    return code


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="hpc-pilot",
        description="HPC Pilot — AI agent for HPC cluster management",
    )
    parser.add_argument(
        "--cluster",
        default=None,
        help="Target cluster (default from config, or $HPC_PILOT_CLUSTER)",
    )
    subs = parser.add_subparsers(dest="command", help="Available commands")

    # chat
    chat_p = subs.add_parser("chat", help="Start interactive AI chat")
    chat_p.add_argument("-q", "--query", help="Single query")
    chat_p.add_argument("-m", "--model", help="Model name")
    chat_p.add_argument("--resume", metavar="SESSION-ID", help="Resume a previous session")
    chat_p.add_argument(
        "--list-sessions", action="store_true", dest="list_sessions",
        help="List saved sessions",
    )
    chat_p.add_argument(
        "--no-summarize", action="store_true", dest="no_summarize",
        help="Disable conversation summarization",
    )
    chat_p.set_defaults(func=chat_command)

    # shell
    shell_p = subs.add_parser("shell", help="Start shell session (alias for chat with --role)")
    shell_p.add_argument("--actor", default="cli-user", help="Operator identity")
    shell_p.add_argument("--role", default="operator", help="RBAC role")
    shell_p.set_defaults(func=shell_command)

    # tui (not yet implemented)
    tui_p = subs.add_parser("tui", help="[planned] Start text-based UI")
    tui_p.set_defaults(func=tui_command)

    # gateway
    gw_p = subs.add_parser("gateway", help="Gateway service control (Telegram + Discord)")
    gw_p.add_argument("--start", action="store_true")
    gw_p.add_argument("--stop", action="store_true")
    gw_p.add_argument("--status", action="store_true")
    gw_p.add_argument("--setup", action="store_true")
    gw_p.add_argument("--port", type=int, default=8000)
    gw_p.add_argument("--host", default="127.0.0.1")
    gw_p.set_defaults(func=gateway_command)

    # webui
    webui_p = subs.add_parser("webui", help="Launch web UI (FastAPI)")
    webui_p.add_argument("--start", action="store_true", help="Start the web UI")
    webui_p.add_argument("--port", type=int, default=0, help="Port (default: 8000, or $HPC_PILOT_PORT)")
    webui_p.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    webui_p.set_defaults(func=webui_command)

    # setup
    setup_p = subs.add_parser("setup", help="Initialize configuration directory")
    setup_p.set_defaults(func=setup_command)

    # health
    health_p = subs.add_parser("health", help="Check cluster health")
    health_p.set_defaults(func=health_command)

    # nodes
    nodes_p = subs.add_parser("nodes", help="Show Slurm node status")
    nodes_p.add_argument("node", nargs="?", default="", help="Node name (omit for all)")
    nodes_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    nodes_p.set_defaults(func=nodes_command)

    # queue
    queue_p = subs.add_parser("queue", help="Show job queue")
    queue_p.add_argument("--user", help="Filter by user")
    queue_p.add_argument("--partition", help="Filter by partition")
    queue_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
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
    ww_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ww_p.set_defaults(func=warewulf_command)

    # spack
    spack_p = subs.add_parser("spack", help="Spack queries")
    spack_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON (env list only)")
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

    # reservation
    res_p = subs.add_parser("reservation", help="Manage Slurm reservations")
    res_subs = res_p.add_subparsers(dest="action")

    res_list_p = res_subs.add_parser("list", help="List reservations")
    res_list_p.set_defaults(func=reservation_command)

    res_create_p = res_subs.add_parser("create", help="Create a reservation")
    res_create_p.add_argument("name", help="Reservation name")
    res_create_p.add_argument("--nodes", required=True, help="Node list or range")
    res_create_p.add_argument("--start", required=True, help="Start time (e.g. 'now')")
    res_create_p.add_argument("--duration", required=True, help="Duration (e.g. '4:00:00')")
    res_create_p.add_argument("--users", default="", help="Comma-separated users")
    res_create_p.add_argument("--accounts", default="", help="Comma-separated accounts")
    res_create_p.add_argument("--flags", default="", help="Reservation flags")
    res_create_p.add_argument("--apply", action="store_true", help="Execute (default: dry-run)")
    res_create_p.set_defaults(func=reservation_command)

    res_update_p = res_subs.add_parser("update", help="Update a reservation")
    res_update_p.add_argument("name", help="Reservation name")
    res_update_p.add_argument("--nodes", default="")
    res_update_p.add_argument("--start", default="")
    res_update_p.add_argument("--duration", default="")
    res_update_p.add_argument("--users", default="")
    res_update_p.add_argument("--flags", default="")
    res_update_p.add_argument("--apply", action="store_true", help="Execute (default: dry-run)")
    res_update_p.set_defaults(func=reservation_command)

    res_del_p = res_subs.add_parser("delete", help="Delete a reservation")
    res_del_p.add_argument("name", help="Reservation name")
    res_del_p.add_argument("--apply", action="store_true", help="Execute (default: dry-run)")
    res_del_p.add_argument("--yes", action="store_true", help="Skip confirmation")
    res_del_p.set_defaults(func=reservation_command)

    res_p.set_defaults(func=reservation_command)

    # account
    acc_p = subs.add_parser("account", help="Manage Slurm accounting accounts")
    acc_subs = acc_p.add_subparsers(dest="action")

    acc_list_p = acc_subs.add_parser("list", help="List accounts")
    acc_list_p.set_defaults(func=account_command)

    acc_create_p = acc_subs.add_parser("create", help="Create an account")
    acc_create_p.add_argument("name", help="Account name")
    acc_create_p.add_argument("--description", default="")
    acc_create_p.add_argument("--organization", default="")
    acc_create_p.add_argument("--apply", action="store_true", help="Execute (default: dry-run)")
    acc_create_p.add_argument("--yes", action="store_true", help="Skip confirmation")
    acc_create_p.set_defaults(func=account_command)

    acc_p.set_defaults(func=account_command)

    # accounting
    acctg_p = subs.add_parser("accounting", help="Query Slurm job accounting history")
    acctg_p.add_argument("--user", help="Filter by user")
    acctg_p.add_argument("--account", help="Filter by account")
    acctg_p.add_argument("--start", help="Start date (e.g. 2026-06-01)")
    acctg_p.add_argument("--end", help="End date")
    acctg_p.add_argument("--state", help="Job state filter (e.g. FAILED,TIMEOUT)")
    acctg_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    acctg_p.set_defaults(func=accounting_command)

    # sdiag
    sdiag_p = subs.add_parser("sdiag", help="Show Slurm scheduler diagnostics")
    sdiag_p.set_defaults(func=sdiag_command)

    # cron (NYI)
    cron_p = subs.add_parser("cron", help="[planned] Scheduled cluster monitoring")
    cron_p.set_defaults(func=cron_command)

    # approve
    approve_p = subs.add_parser("approve", help="Approve or reject out-of-band approval requests")
    approve_p.add_argument("request_id", nargs="?", help="Approval request ID")
    approve_p.add_argument("--reject", action="store_true", help="Reject instead of approve")
    approve_p.add_argument("--list", action="store_true", dest="list_approvals", help="List pending approvals")
    approve_p.set_defaults(func=approve_command)

    # version
    version_p = subs.add_parser("version", help="Show version")
    version_p.set_defaults(func=version_command)

    args = parser.parse_args(argv)

    if not args.command:
        args.func = chat_command

    func: Callable[[argparse.Namespace], int] | None = getattr(args, "func", None)
    if func is not None:
        return func(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
