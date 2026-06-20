"""Gateway and Web UI CLI subcommands."""

from __future__ import annotations

import argparse
import contextlib
import os
import signal
import sys
import time
from typing import Any


def gateway_command(args: argparse.Namespace) -> int:
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
    return _gateway_start(args, pid_path)


def webui_command(args: argparse.Namespace) -> int:
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
    from hpc_pilot.gateway import main as gateway_main
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
    for _ in range(100):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except OSError:
            with contextlib.suppress(OSError):
                os.remove(pid_path)
            print("Gateway stopped.")
            return 0
    print("Gateway did not stop gracefully; sending SIGKILL.", file=sys.stderr)
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.5)
        with contextlib.suppress(OSError):
            os.remove(pid_path)
    print("Gateway killed.")
    return 0


def _gateway_status(pid_path: str) -> int:
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
