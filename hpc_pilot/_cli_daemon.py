"""Daemon CLI subcommand — start gateway + webui + scheduled health together."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess as sp
import sys
import time
from typing import Any


def daemon_command(args: argparse.Namespace) -> int:
    """Run gateway, webui, and scheduled health check as a single daemon process."""
    from hpc_pilot.paths import get_home

    home = get_home()
    pid_dir = os.path.join(home, "run")
    os.makedirs(pid_dir, exist_ok=True)

    if getattr(args, "stop", False):
        return _daemon_stop(pid_dir)
    if getattr(args, "status", False):
        return _daemon_status(pid_dir)

    return _daemon_start(args, pid_dir)


def _pid_path(pid_dir: str, name: str) -> str:
    return os.path.join(pid_dir, f"{name}.pid")


def _write_pid(pid_dir: str, name: str, pid: int) -> None:
    with open(_pid_path(pid_dir, name), "w") as f:
        f.write(str(pid))


def _read_pid(pid_dir: str, name: str) -> int | None:
    p = _pid_path(pid_dir, name)
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def _is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _clean_pid(pid_dir: str, name: str) -> None:
    import contextlib

    p = _pid_path(pid_dir, name)
    with contextlib.suppress(OSError):
        os.remove(p)


def _daemon_stop(pid_dir: str) -> int:
    components = ["gateway", "webui", "monitor"]
    any_found = False
    for name in components:
        pid = _read_pid(pid_dir, name)
        if pid is None or not _is_alive(pid):
            _clean_pid(pid_dir, name)
            continue
        any_found = True
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(50):
                if not _is_alive(pid):
                    break
                time.sleep(0.1)
            else:
                os.kill(pid, signal.SIGKILL)
            print(f"Stopped {name} (PID {pid})")
        except PermissionError:
            print(f"Permission denied: cannot stop {name} (PID {pid})", file=sys.stderr)
            return 1
        _clean_pid(pid_dir, name)

    if not any_found:
        print("Daemon: NOT RUNNING", file=sys.stderr)
        return 1
    return 0


def _daemon_status(pid_dir: str) -> int:
    components = ["gateway", "webui", "monitor"]
    all_running = True
    for name in components:
        pid = _read_pid(pid_dir, name)
        if pid is not None and _is_alive(pid):
            print(f"{name}: RUNNING (PID {pid})")
        else:
            _clean_pid(pid_dir, name)
            print(f"{name}: NOT RUNNING", file=sys.stderr)
            all_running = False
    return 0 if all_running else 1


def _monitor_loop(pid_dir: str, cluster: str, interval: int) -> None:
    """Health-check monitor subprocess — runs until killed.

    Logs to ``~/.hpc-pilot/cron.jsonl`` (same file as ``hpc-pilot cron``).
    """
    from hpc_pilot._cli_base import _get_actor
    from hpc_pilot.dispatch import invoke
    from hpc_pilot.paths import get_home
    from hpc_pilot.rbac import get_role

    log_file = os.path.join(get_home(), "cron.jsonl")
    while True:
        try:
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
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "cluster": cluster,
                "result": result,
            }
            with open(log_file, "a") as f:
                f.write(json.dumps(record) + "\n")

            overall = result.get("overall", "unknown")
            issues = result.get("issues", [])
            msg = f"[monitor] health={overall}"
            if issues:
                msg += f" {len(issues)} issue(s)"
            print(msg, file=sys.stderr)
        except Exception as exc:
            print(f"[monitor] Error: {exc}", file=sys.stderr)
        time.sleep(interval)


def _daemon_start(args: argparse.Namespace, pid_dir: str) -> int:
    from hpc_pilot._cli_base import _resolve_cluster_flag

    cluster = _resolve_cluster_flag(getattr(args, "cluster", None))
    port: int = getattr(args, "port", 0) or int(os.environ.get("HPC_PILOT_PORT", "8000"))
    host: str = getattr(args, "host", "127.0.0.1")
    interval: int = getattr(args, "interval", 300)

    # Check for existing processes
    for name in ("gateway", "webui"):
        pid = _read_pid(pid_dir, name)
        if pid is not None and _is_alive(pid):
            print(f"{name} is already running (PID {pid}). Use --stop first.", file=sys.stderr)
            return 1

    # We are the daemon — detach by forking
    pid = os.fork()
    if pid > 0:
        # Parent: exit so the shell returns control
        print(
            f"Daemon started (PID {pid}). " "Use 'hpc-pilot daemon --status' to check.",
            file=sys.stderr,
        )
        return 0

    # Child: become session leader, fork again to fully detach
    os.setsid()
    pid2 = os.fork()
    if pid2 > 0:
        os._exit(0)

    # Grandchild: the actual daemon
    _write_pid(pid_dir, "daemon", os.getpid())

    # Redirect stdio to /dev/null
    sys.stdin.close()
    sys.stdout.close()
    sys.stderr.close()
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    os.dup2(devnull, 2)
    os.close(devnull)

    processes: dict[str, sp.Popen[Any]] = {}

    try:
        # Start gateway
        processes["gateway"] = sp.Popen(
            [sys.executable, "-m", "hpc_pilot.gateway", "--start"],
            stdout=sp.DEVNULL,
            stderr=sp.DEVNULL,
        )
        _write_pid(pid_dir, "gateway", processes["gateway"].pid)

        # Start webui
        webui_env = os.environ.copy()
        if port:
            webui_env["HPC_PILOT_PORT"] = str(port)
        processes["webui"] = sp.Popen(
            [
                sys.executable,
                "-c",
                f"import sys; sys.argv = ['hpc-pilot', 'webui']; "
                f"from hpc_pilot.webui import run_webui; run_webui(host='{host}', port={port})",
            ],
            stdout=sp.DEVNULL,
            stderr=sp.DEVNULL,
            env=webui_env,
        )
        _write_pid(pid_dir, "webui", processes["webui"].pid)

        # Start monitor in-process (we're already detached)
        _monitor_loop(pid_dir, cluster, interval)

    except BaseException:
        # Ensure children are cleaned up on unexpected exit
        for _name, proc in processes.items():
            proc.terminate()
        _clean_pid(pid_dir, "daemon")
        raise
