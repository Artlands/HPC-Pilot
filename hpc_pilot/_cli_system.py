"""System CLI subcommands: health, warewulf, spack, ansible."""

from __future__ import annotations

import argparse
import json
from typing import Any

from hpc_pilot._cli_base import _get_actor, _confirm, _invoke_cli, ensure_home
from hpc_pilot.rbac import get_role


def health_command(args: argparse.Namespace) -> int:
    ensure_home()
    result, code = _invoke_cli("hpc_cluster_health_check", {}, get_role(), _get_actor(), cli_args=args)
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
