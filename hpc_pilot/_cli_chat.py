"""Chat/interactive CLI subcommands: chat, shell, cron, tui."""

from __future__ import annotations

import argparse
import os
import sys

from hpc_pilot._cli_base import _make_agent, ensure_home


def chat_command(args: argparse.Namespace) -> int:
    ensure_home()
    from hpc_pilot.config import init_config
    init_config()

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

    from hpc_pilot.agent import _load_env
    _load_env()

    agent = _make_agent(args)

    query: str | None = getattr(args, "query", None)
    if query:
        try:
            text = agent.run_query(query)
            print(text)
            return 0
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    from hpc_pilot.agent import run_chat_loop
    return run_chat_loop(agent, initial_history=None)


def shell_command(args: argparse.Namespace) -> int:
    role_arg = getattr(args, "role", None)
    if role_arg:
        os.environ["HPC_PILOT_ROLE"] = role_arg
    actor_arg = getattr(args, "actor", None)
    if actor_arg:
        os.environ["HPC_PILOT_ACTOR"] = actor_arg
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
