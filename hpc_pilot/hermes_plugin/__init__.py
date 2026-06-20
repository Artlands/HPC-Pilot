"""HPC Pilot Hermes Agent plugin — registers HPC cluster management tools.

All 114 hpc_* tools from the hpc_pilot package are registered as a Hermes
toolset named "hpc". Each tool call flows through:

1. Hermes tool registry dispatch
2. HPC-Pilot RBAC check (check_permission -> PermissionError)
3. HPC-Pilot audit logging (audit_tool context manager)
4. The actual hpc_* tool function (via dispatch.invoke)

The plugin is model-agnostic — Hermes handles provider translation internally.

Install
-------
    pip install hpc-pilot
    hpc-pilot setup-hermes    # symlinks this plugin to ~/.hermes/plugins/hpc-pilot/
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# Schema conversion: Anthropic input_schema -> OpenAI parameters
# ---------------------------------------------------------------


def _to_openai_schema(entry: dict) -> dict:
    """Convert an Anthropic-format tool schema entry to OpenAI function-calling format.

    Anthropic format (from agent.py TOOL_SCHEMAS)::
        {"name": ..., "description": ..., "input_schema": {"type": "object", ...}}

    OpenAI format (what Hermes expects)::
        {"name": ..., "description": ..., "parameters": {"type": "object", ...}}
    """
    return {
        "name": entry["name"],
        "description": entry.get("description", ""),
        "parameters": {
            "type": "object",
            "properties": entry.get("input_schema", {}).get("properties", {}),
            "required": entry.get("input_schema", {}).get("required", []),
        },
    }


# ---------------------------------------------------------------
# Handler builder: inject RBAC + audit + dispatch
# ---------------------------------------------------------------


def _make_handler(tool_name: str) -> Callable:
    """Build a Hermes-compatible handler that calls dispatch.invoke with RBAC + audit."""

    def handler(args: dict, **kwargs) -> str:
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import get_role

        role = get_role()
        actor = os.environ.get("HPC_PILOT_ACTOR", "hermes")

        # Resolve defaults
        effective_args = dict(args)
        effective_args.setdefault("cluster", os.environ.get("HPC_PILOT_CLUSTER", "default"))
        effective_args.setdefault("dry_run", True)

        try:
            result = invoke(
                tool_name, effective_args,
                role=role, actor=actor,
                dry_run=bool(effective_args.get("dry_run", False)),
            )
            return result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
        except PermissionError as exc:
            return json.dumps({"error": f"Permission denied: {exc}"})
        except ValueError as exc:
            return json.dumps({"error": f"Input error: {exc}"})
        except Exception as exc:
            return json.dumps({"error": f"Tool error: {exc}"})

    return handler


# ---------------------------------------------------------------
# Availability check helpers
# ---------------------------------------------------------------


def _make_availability_check(category: str) -> Callable:
    """Return a check_fn that probes a cluster subsystem availability."""
    _fn_registry: dict[str, str] = {
        "slurm": "check_slurm_available",
        "warewulf": "check_warewulf_available",
        "spack": "check_spack_available",
        "ansible": "check_ansible_available",
    }
    check_name = _fn_registry.get(category)
    if check_name is None:
        return lambda: True

    def _check() -> bool:
        try:
            from hpc_pilot.tools._run import (
                check_ansible_available,
                check_slurm_available,
                check_spack_available,
                check_warewulf_available,
            )
            fn = {
                "check_slurm_available": check_slurm_available,
                "check_warewulf_available": check_warewulf_available,
                "check_spack_available": check_spack_available,
                "check_ansible_available": check_ansible_available,
            }[check_name]
            return bool(fn())
        except Exception:
            return False

    return _check


# ---------------------------------------------------------------
# Tool -> category mapping for availability checks
# ---------------------------------------------------------------


def _tool_category(name: str) -> str | None:
    if name.startswith("hpc_slurm") or name.startswith("hpc_logs"):
        return "slurm"
    if name.startswith("hpc_warewulf"):
        return "warewulf"
    if name.startswith("hpc_spack"):
        return "spack"
    if name.startswith("hpc_ansible"):
        return "ansible"
    if name in ("hpc_job_status", "hpc_job_logs"):
        return None  # always available
    return None


# ---------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------


def register(ctx) -> None:
    """Register all HPC-Pilot tools as a Hermes toolset named 'hpc'.

    Called by the Hermes Agent plugin system at startup.

    The ``ctx`` object must have a ``register_tool()`` method matching
    the Hermes ``PluginContext`` signature.
    """
    from hpc_pilot.agent import TOOL_SCHEMAS

    registered = 0
    for entry in TOOL_SCHEMAS:
        tool_name = entry["name"]

        schema = _to_openai_schema(entry)
        handler = _make_handler(tool_name)

        cat = _tool_category(tool_name)
        check_fn = _make_availability_check(cat) if cat else None

        ctx.register_tool(
            name=tool_name,
            toolset="hpc",
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            emoji="\U0001f527",  # wrench
            override=False,
        )
        registered += 1

    try:
        from tools.registry import registry as _reg

        _reg.register_toolset_alias("hpc-pilot", "hpc")
    except ImportError:
        pass

    logger.info("HPC Pilot plugin registered %d tools in toolset 'hpc'", registered)


# ---------------------------------------------------------------
# Plugin directory discovery — used by ``hpc-pilot setup-hermes``
# ---------------------------------------------------------------


def plugin_dir() -> str:
    """Return the absolute path to the Hermes plugin directory in this package."""
    return os.path.dirname(os.path.abspath(__file__))
