#!/usr/bin/env python3
"""
HPC Pilot CLI — command-line interface for HPC cluster management.

AI agent commands (require ANTHROPIC_API_KEY):
    hpc-pilot chat           Interactive AI chat session
    hpc-pilot chat -q TEXT   Single AI query, non-interactive
    hpc-pilot shell          Shell session (alias for chat with --actor/--role)

Setup commands:
    hpc-pilot setup          Create ~/.hpc-pilot/ directory and default config
    hpc-pilot setup-hermes   Install Hermes Agent plugin (symlink)
    hpc-pilot config set     Set Hermes Agent configuration
    hpc-pilot config list    Show Hermes Agent configuration

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
import sys
from collections.abc import Callable

from hpc_pilot._cli_admin import (
    approve_command,
    audit_prune_command,
    config_command,
    self_evolve_command,
    self_evolve_create_pr_command,
    setup_command,
    setup_hermes_command,
    version_command,
)
from hpc_pilot._cli_base import (  # noqa: F401 — re-exported for tests
    config_file,
    ensure_home,
    home_dir,
    init_config,
)
from hpc_pilot._cli_chat import chat_command, cron_command, shell_command, tui_command
from hpc_pilot._cli_daemon import daemon_command
from hpc_pilot._cli_gateway import gateway_command, webui_command
from hpc_pilot._cli_slurm import (
    account_command,
    accounting_command,
    nodes_command,
    qos_command,
    queue_command,
    reservation_command,
    sdiag_command,
)
from hpc_pilot._cli_system import ansible_command, health_command, spack_command, warewulf_command


def _add_cluster_arg(p: argparse.ArgumentParser) -> None:
    """Add a --cluster argument to *p* so it shows up in the subcommand's --help."""
    p.add_argument(
        "--cluster",
        default=None,
        help="Target cluster (default from config, or $HPC_PILOT_CLUSTER)",
    )


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
    _add_cluster_arg(chat_p)
    chat_p.add_argument("-q", "--query", help="Single query")
    chat_p.add_argument("-m", "--model", help="Model name")
    chat_p.add_argument("--resume", metavar="SESSION-ID", help="Resume a previous session")
    chat_p.add_argument(
        "--list-sessions",
        action="store_true",
        dest="list_sessions",
        help="List saved sessions",
    )
    chat_p.set_defaults(func=chat_command)

    # shell
    shell_p = subs.add_parser("shell", help="Start shell session (alias for chat with --role)")
    _add_cluster_arg(shell_p)
    shell_p.add_argument("--actor", default="cli-user", help="Operator identity")
    shell_p.add_argument("--role", default="operator", help="RBAC role")
    shell_p.set_defaults(func=shell_command)

    # tui
    tui_p = subs.add_parser("tui", help="Start text-based UI dashboard")
    tui_p.add_argument(
        "--cluster",
        default=None,
        help="Target cluster (default from config, or $HPC_PILOT_CLUSTER)",
    )
    tui_p.set_defaults(func=tui_command)

    # gateway
    gw_p = subs.add_parser("gateway", help="Gateway service control (Telegram + Discord)")
    _add_cluster_arg(gw_p)
    gw_p.add_argument("--start", action="store_true")
    gw_p.add_argument("--stop", action="store_true")
    gw_p.add_argument("--status", action="store_true")
    gw_p.add_argument("--setup", action="store_true")
    gw_p.add_argument("--port", type=int, default=8000)
    gw_p.add_argument("--host", default="127.0.0.1")
    gw_p.set_defaults(func=gateway_command)

    # daemon
    daemon_p = subs.add_parser(
        "daemon",
        help="Start/stop all services (gateway + webui + health monitor) as a daemon",
    )
    _add_cluster_arg(daemon_p)
    daemon_p.add_argument("--start", action="store_true", help="Start the daemon")
    daemon_p.add_argument("--stop", action="store_true", help="Stop the daemon")
    daemon_p.add_argument("--status", action="store_true", help="Check daemon status")
    daemon_p.add_argument(
        "--port", type=int, default=0, help="Web UI port (default: 8000, or $HPC_PILOT_PORT)"
    )
    daemon_p.add_argument("--host", default="127.0.0.1", help="Web UI host (default: 127.0.0.1)")
    daemon_p.add_argument(
        "--interval", type=int, default=300, help="Health check interval in seconds (default: 300)"
    )
    daemon_p.set_defaults(func=daemon_command)

    # webui
    webui_p = subs.add_parser("webui", help="Launch web UI (FastAPI)")
    _add_cluster_arg(webui_p)
    webui_p.add_argument("--start", action="store_true", help="Start the web UI")
    webui_p.add_argument(
        "--port", type=int, default=0, help="Port (default: 8000, or $HPC_PILOT_PORT)"
    )
    webui_p.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    webui_p.set_defaults(func=webui_command)

    # setup
    setup_p = subs.add_parser("setup", help="Initialize configuration directory")
    _add_cluster_arg(setup_p)
    setup_p.set_defaults(func=setup_command)

    # health
    health_p = subs.add_parser("health", help="Check cluster health")
    _add_cluster_arg(health_p)
    health_p.set_defaults(func=health_command)

    # nodes
    nodes_p = subs.add_parser("nodes", help="Show Slurm node status")
    _add_cluster_arg(nodes_p)
    nodes_p.add_argument("node", nargs="?", default="", help="Node name (omit for all)")
    nodes_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    nodes_p.set_defaults(func=nodes_command)

    # queue
    queue_p = subs.add_parser("queue", help="Show job queue")
    _add_cluster_arg(queue_p)
    queue_p.add_argument("--user", help="Filter by user")
    queue_p.add_argument("--partition", help="Filter by partition")
    queue_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    queue_p.set_defaults(func=queue_command)

    # qos
    qos_p = subs.add_parser("qos", help="Inspect or modify a Slurm QOS")
    _add_cluster_arg(qos_p)
    qos_p.add_argument("name", help="QOS name")
    qos_p.add_argument("--max-wall-min", type=int, dest="max_wall_min", help="Max wall time (min)")
    qos_p.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    qos_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    qos_p.set_defaults(func=qos_command)

    # warewulf
    ww_p = subs.add_parser("warewulf", help="Show Warewulf node status")
    _add_cluster_arg(ww_p)
    ww_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    ww_p.set_defaults(func=warewulf_command)

    # spack
    spack_p = subs.add_parser("spack", help="Spack queries")
    _add_cluster_arg(spack_p)
    spack_p.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON (env list only)"
    )
    spack_subs = spack_p.add_subparsers(dest="action")
    spack_subs.add_parser("list", help="List environments")
    spack_find_p = spack_subs.add_parser("find", help="List specs in an environment")
    spack_find_p.add_argument("env", help="Environment name")
    spack_subs.add_parser("compilers", help="List compilers")
    spack_p.set_defaults(func=spack_command)

    # ansible
    ansible_p = subs.add_parser("ansible", help="Run an Ansible playbook")
    _add_cluster_arg(ansible_p)
    ansible_p.add_argument("playbook", help="Path to playbook YAML")
    ansible_p.add_argument("--limit", help="Host limit")
    ansible_p.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    ansible_p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    ansible_p.add_argument("--check", action="store_true", help="Ansible check mode")
    ansible_p.set_defaults(func=ansible_command)

    # reservation
    res_p = subs.add_parser("reservation", help="Manage Slurm reservations")
    _add_cluster_arg(res_p)
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
    _add_cluster_arg(acc_p)
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
    _add_cluster_arg(acctg_p)
    acctg_p.add_argument("--user", help="Filter by user")
    acctg_p.add_argument("--account", help="Filter by account")
    acctg_p.add_argument("--start", help="Start date (e.g. 2026-06-01)")
    acctg_p.add_argument("--end", help="End date")
    acctg_p.add_argument("--state", help="Job state filter (e.g. FAILED,TIMEOUT)")
    acctg_p.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    acctg_p.set_defaults(func=accounting_command)

    # sdiag
    sdiag_p = subs.add_parser("sdiag", help="Show Slurm scheduler diagnostics")
    _add_cluster_arg(sdiag_p)
    sdiag_p.set_defaults(func=sdiag_command)

    # cron (scheduled monitoring)
    cron_p = subs.add_parser("cron", help="Scheduled cluster health monitoring")
    cron_p.add_argument(
        "--interval", type=int, default=300, help="Polling interval in seconds (default: 300)"
    )
    cron_p.add_argument(
        "--cluster",
        default=None,
        help="Target cluster (default from config, or $HPC_PILOT_CLUSTER)",
    )
    cron_p.set_defaults(func=cron_command)

    # approve
    approve_p = subs.add_parser("approve", help="Approve or reject out-of-band approval requests")
    _add_cluster_arg(approve_p)
    approve_p.add_argument("request_id", nargs="?", help="Approval request ID")
    approve_p.add_argument("--reject", action="store_true", help="Reject instead of approve")
    approve_p.add_argument(
        "--list", action="store_true", dest="list_approvals", help="List pending approvals"
    )
    approve_p.set_defaults(func=approve_command)

    # version
    version_p = subs.add_parser("version", help="Show version")
    _add_cluster_arg(version_p)
    version_p.set_defaults(func=version_command)

    # self-evolve
    evolve_p = subs.add_parser(
        "self-evolve",
        help="Generate a new tool, register it, and run tests (no git/PR)",
    )
    evolve_p.add_argument("tool_name", help="Name for the new tool")
    evolve_p.add_argument("--description", required=True, help="Human-readable description")
    evolve_p.add_argument("--code", required=True, help="Python function body for the tool")
    evolve_p.add_argument("--test-code", required=True, dest="test_code", help="Pytest test code")
    evolve_p.add_argument("--schema", default="{}", help="JSON schema for input parameters")
    evolve_p.add_argument(
        "--role", default="VIEWER", help="RBAC role (VIEWER/OPERATOR/ADMIN/SUPERADMIN)"
    )
    evolve_p.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Preview without writing"
    )
    _add_cluster_arg(evolve_p)
    evolve_p.set_defaults(func=self_evolve_command)
    evolve_pr_p = subs.add_parser(
        "self-evolve-create-pr",
        help="Commit, push, and open a PR for an evolved tool",
    )
    evolve_pr_p.add_argument("tool_name", help="Name of the evolved tool")
    evolve_pr_p.add_argument(
        "--description", default="", help="Human-readable description for the PR body"
    )
    evolve_pr_p.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Preview without pushing"
    )
    _add_cluster_arg(evolve_pr_p)
    evolve_pr_p.set_defaults(func=self_evolve_create_pr_command)

    # config
    config_p = subs.add_parser("config", help="Get or set Hermes Agent configuration values")
    config_subs = config_p.add_subparsers(dest="action")
    config_set_p = config_subs.add_parser(
        "set", help="Set a config value (e.g. providers.local.base_url)"
    )
    config_set_p.add_argument("key", help="Config key path")
    config_set_p.add_argument("value", help="Config value")
    config_set_p.set_defaults(func=config_command)
    config_get_p = config_subs.add_parser("get", help="Show full configuration")
    config_get_p.add_argument("key", nargs="?", help="Config key (optional, shows all if omitted)")
    config_get_p.set_defaults(func=config_command)
    config_list_p = config_subs.add_parser("list", help="Show full configuration")
    config_list_p.set_defaults(func=config_command)
    config_show_p = config_subs.add_parser("show", help="Show full configuration")
    config_show_p.set_defaults(func=config_command)

    config_reload_p = config_subs.add_parser(
        "reload",
        help="Reload configuration from config.yaml (cluster cache, audit sinks, rate limiter)",
    )
    config_reload_p.set_defaults(func=config_command)
    _add_cluster_arg(config_p)
    config_p.set_defaults(func=config_command)

    # audit-prune
    audit_prune_p = subs.add_parser(
        "audit-prune",
        help="Remove audit log entries older than N days",
    )
    audit_prune_p.add_argument(
        "--older-than",
        type=int,
        default=90,
        help="Remove entries older than this many days (default: 90)",
    )
    _add_cluster_arg(audit_prune_p)
    audit_prune_p.set_defaults(func=audit_prune_command)

    # setup-hermes
    setup_hermes_p = subs.add_parser(
        "setup-hermes",
        help="Install the HPC-Pilot Hermes Agent plugin",
    )
    _add_cluster_arg(setup_hermes_p)
    setup_hermes_p.set_defaults(func=setup_hermes_command)

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
