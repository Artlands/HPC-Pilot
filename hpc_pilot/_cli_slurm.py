"""Slurm CLI subcommands: nodes, queue, qos, reservations, accounts, accounting, sdiag."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from hpc_pilot._cli_base import _get_actor, _confirm, _invoke_cli, ensure_home
from hpc_pilot.rbac import get_role


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
