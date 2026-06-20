"""Chat/interactive CLI subcommands: chat, shell, cron, tui."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

from hpc_pilot._cli_base import _get_actor, _make_agent, _resolve_cluster_flag, ensure_home
from hpc_pilot.rbac import get_role


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
    """Run scheduled cluster health monitoring on a loop.

    Accepts --interval (default 300 s) and --cluster to specify the target.
    Each cycle runs the full health check tool and writes a JSON line to
    ``~/.hpc-pilot/cron.jsonl`` for downstream alert consumption.
    """
    interval = getattr(args, "interval", 300)
    cluster = _resolve_cluster_flag(getattr(args, "cluster", None))
    log_path = ensure_home()
    log_file = os.path.join(log_path, "cron.jsonl")

    print(f"Cron monitoring started — cluster={cluster} interval={interval}s", file=sys.stderr)
    print(f"Logging to {log_file}", file=sys.stderr)
    print("Press Ctrl+C to stop.", file=sys.stderr)

    while True:
        try:
            from hpc_pilot.dispatch import invoke

            raw = invoke(
                "hpc_cluster_health_check",
                {"cluster": cluster},
                role=get_role(),
                actor=_get_actor(),
            )
            try:
                result = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                result = {"raw": str(raw), "overall": "unknown", "issues": []}

            record = {
                "ts": datetime.datetime.now().isoformat(),
                "cluster": cluster,
                "result": result,
            }
            with open(log_file, "a") as f:
                f.write(json.dumps(record) + "\n")

            overall = result.get("overall", "unknown")
            issues = result.get("issues", [])
            status = f"[{overall}]"
            if issues:
                status += f" {len(issues)} issue(s)"
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {status}", file=sys.stderr)

        except KeyboardInterrupt:
            print("\nCron monitoring stopped.", file=sys.stderr)
            return 0
        except Exception as exc:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Error: {exc}", file=sys.stderr)

        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("\nCron monitoring stopped.", file=sys.stderr)
            return 0


def tui_command(args: argparse.Namespace) -> int:
    """Launch a text-based UI showing cluster status at a glance.

    Uses ``rich`` (optional dependency). Install with::
        pip install 'hpc-pilot[tui]'

    Displays a live-updating dashboard with cluster health, node counts,
    queue summary, and recent issues.
    """
    cluster = _resolve_cluster_flag(getattr(args, "cluster", None))

    try:
        from rich.layout import Layout
        from rich.live import Live
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        print(
            "hpc-pilot tui requires 'rich'. Install with: pip install 'hpc-pilot[tui]'",
            file=sys.stderr,
        )
        return 1

    from hpc_pilot.dispatch import invoke

    def _make_dashboard() -> Layout:
        layout = Layout()
        layout.split_column(Layout(name="header", size=3), Layout(name="body"))

        # Header
        header = Panel(
            Text(f"HPC Pilot — Cluster: {cluster}", style="bold cyan"),
            style="bright_blue",
        )
        layout["header"].update(header)

        # Try to fetch live data
        health_raw = invoke(
            "hpc_cluster_health_check",
            {"cluster": cluster},
            role=get_role(),
            actor=_get_actor(),
        )
        try:
            health_result = json.loads(health_raw)
        except (json.JSONDecodeError, TypeError):
            health_result = {}

        nodes_raw = invoke(
            "hpc_slurm_node_status",
            {"node": "", "json": False},
            role=get_role(),
            actor=_get_actor(),
        )

        body = Table.grid()
        body.add_column()

        if health_result:
            overall = health_result.get("overall", "unknown")
            color = (
                "green" if overall == "healthy" else "yellow" if overall == "degraded" else "red"
            )
            comps = health_result.get("components", {})
            issues = health_result.get("issues", [])

            summary = Table(box=None)
            summary.add_column("Component", style="cyan")
            summary.add_column("Status")
            for name, info in comps.items():
                status = info.get("status", "?")
                sc = (
                    "green"
                    if status in ("healthy",)
                    else "yellow" if status in ("degraded", "checking") else "red"
                )
                summary.add_row(name, Text(status, style=sc))

            health_panel = Panel(
                summary,
                title=Text(f"Health: {overall}", style=color),
                border_style=color,
            )
            body.add_row(health_panel)

            if issues:
                issue_text = "\n".join(f"  • {i}" for i in issues[:5])
                if len(issues) > 5:
                    issue_text += f"\n  ... and {len(issues) - 5} more"
                body.add_row(Panel(issue_text, title="Issues", style="yellow"))

        if nodes_raw and isinstance(nodes_raw, str):
            body.add_row(Panel(nodes_raw[:2000], title="Node Status", style="dim"))

        layout["body"].update(body)
        return layout

    try:
        with Live(_make_dashboard(), refresh_per_second=2, screen=True) as live:
            while True:
                time.sleep(5)
                live.update(_make_dashboard())
    except KeyboardInterrupt:
        pass
    return 0
