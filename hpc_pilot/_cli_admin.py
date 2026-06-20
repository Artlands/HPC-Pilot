"""Admin CLI subcommands: setup, version, approve, config, self-evolve, audit-prune, hermes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess as sp
import sys
from typing import Any

from hpc_pilot._cli_base import _get_actor, config_file, ensure_home, home_dir


def setup_command(args: argparse.Namespace) -> int:
    ensure_home()
    from hpc_pilot.config import init_config

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


def version_command(args: argparse.Namespace) -> int:
    from hpc_pilot import __version__

    try:
        print(f"HPC Pilot {__version__}")
        print(f"Python: {'.'.join(map(str, sys.version_info[:3]))}")
        return 0
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def approve_command(args: argparse.Namespace) -> int:
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


def audit_prune_command(args: argparse.Namespace) -> int:
    from hpc_pilot.audit import prune_audit_log

    pruned = prune_audit_log(older_than_days=args.older_than)
    print(f"Pruned {pruned} audit log entries older than {args.older_than} days.")
    return 0


def setup_hermes_command(args: argparse.Namespace) -> int:
    hermes_plugins_dir = os.path.expanduser("~/.hermes/plugins")
    link_dir = os.path.join(hermes_plugins_dir, "hpc-pilot")
    from hpc_pilot.hermes_plugin import plugin_dir

    src = plugin_dir()

    if not os.path.exists(src):
        print(f"Plugin source not found: {src}", file=sys.stderr)
        return 1

    os.makedirs(hermes_plugins_dir, exist_ok=True)

    if os.path.islink(link_dir) or os.path.exists(link_dir):
        try:
            if os.path.islink(link_dir):
                os.unlink(link_dir)
            else:
                shutil.rmtree(link_dir)
        except OSError as exc:
            print(f"Error removing existing plugin at {link_dir}: {exc}", file=sys.stderr)
            return 1

    try:
        os.symlink(src, link_dir)
    except OSError:
        try:
            shutil.copytree(src, link_dir, dirs_exist_ok=True)
        except OSError as copy_err:
            print(f"Error installing plugin: {copy_err}", file=sys.stderr)
            return 1

    print(f"HPC-Pilot Hermes plugin installed at {link_dir}")
    print(f"  -> source: {src}")
    print()
    print("Next: set ANTHROPIC_API_KEY (or other provider key) in your environment")
    print("  and run: hpc-pilot chat")
    return 0


def self_evolve_command(args: argparse.Namespace) -> int:
    from hpc_pilot.tools.evolve import hpc_self_evolve

    try:
        schema = json.loads(args.schema)
    except json.JSONDecodeError as exc:
        print(f"Invalid --schema JSON: {exc}", file=sys.stderr)
        return 1
    result = hpc_self_evolve(
        tool_name=args.tool_name,
        description=args.description,
        code=args.code,
        test_code=args.test_code,
        schema=schema,
        required_role=args.role,
        dry_run=args.dry_run,
    )
    print(result)
    return 0


def self_evolve_create_pr_command(args: argparse.Namespace) -> int:
    from hpc_pilot.tools.evolve import hpc_self_evolve_create_pr

    result = hpc_self_evolve_create_pr(
        tool_name=args.tool_name,
        description=args.description,
        dry_run=args.dry_run,
    )
    print(result)
    return 0


def config_command(args: argparse.Namespace) -> int:
    from hpc_pilot.agent import _find_hermes

    key = getattr(args, "key", None)
    value = getattr(args, "value", None)
    hermes_bin = _find_hermes()
    action = getattr(args, "action", None)

    if action == "set" and key and value is not None:
        proc = sp.run([hermes_bin, "config", "set", key, value], capture_output=True, text=True)
        if proc.returncode == 0:
            output = proc.stdout.strip() or "ok"
            print(output)
        else:
            print(proc.stderr.strip() or f"Failed to set {key}", file=sys.stderr)
            return 1
    elif action == "get" and key:
        proc = sp.run([hermes_bin, "config", "show"], capture_output=True, text=True)
        if proc.returncode != 0:
            print(proc.stderr.strip() or "Failed to read config", file=sys.stderr)
            return 1
        try:
            import yaml

            config_data = yaml.safe_load(proc.stdout)
            parts = key.split(".")
            val: Any = config_data
            for p in parts:
                if isinstance(val, dict):
                    val = val.get(p)
                else:
                    val = None
                    break
            if val is None:
                print(f"Key not found: {key}", file=sys.stderr)
                return 1
            if isinstance(val, (dict, list)):
                print(yaml.dump(val, default_flow_style=False).rstrip())
            else:
                print(val)
            return 0
        except ImportError:
            # No yaml library — fall back to full dump
            print(proc.stdout)
            return 0
    elif action == "reload":
        from hpc_pilot.audit import reset_sinks
        from hpc_pilot.clusters import _invalidate_cluster_cache
        from hpc_pilot.dispatch import reset_rate_limiter

        _invalidate_cluster_cache()
        reset_sinks()
        reset_rate_limiter()
        print("Configuration reloaded.")
        return 0
    elif action == "list" or action == "show":
        proc = sp.run([hermes_bin, "config", "show"], capture_output=True, text=True)
        if proc.returncode == 0:
            print(proc.stdout)
        else:
            print(proc.stderr.strip() or "Failed to read config", file=sys.stderr)
            return 1
    else:
        print("Usage:")
        print("  hpc-pilot config set <key> <value>    Set a config value")
        print("  hpc-pilot config get <key>            Show config value for dotted key")
        print("  hpc-pilot config list                 Show config (full)")
        return 0
    return 0
