"""
Placeholder for a future AI agent integration layer.

HPC Pilot's chat / shell / tui / gateway commands require an agent backend.
That integration is not yet implemented.  This module will be the integration
point when it is.

Do NOT import phantom packages (hermes_cli, hermes_agent, tools.registry)
here — they are not declared dependencies and will cause ImportError.
"""
from __future__ import annotations

from hpc_pilot.paths import ensure_layout, get_home, config_path
from hpc_pilot.config import init_config


def _prepare() -> None:
    """Ensure the home directory and default config exist."""
    ensure_layout()
    init_config()


def run_cli(args: list[str]) -> int:
    """Stub: AI agent CLI is not yet implemented."""
    import sys
    print(
        "hpc-pilot: the AI agent layer is not yet implemented.\n"
        "Use the direct cluster commands (nodes, queue, health, …) instead.",
        file=sys.stderr,
    )
    return 1


def run_agent(query: str | None = None, toolsets: list[str] | None = None) -> int:
    """Stub: AI agent runner is not yet implemented."""
    return run_cli([])
