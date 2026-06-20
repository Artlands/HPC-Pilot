"""Single `@hpc_tool` decorator — the canonical tool registry.

Every HPC-Pilot tool is registered in one place via the ``@hpc_tool``
decorator.  The three legacy views (schemas, dispatch, RBAC) are derived
automatically.

Usage::

    from hpc_pilot.tools._registry import hpc_tool

    @hpc_tool(
        name="hpc_slurm_node_status",
        role=Role.VIEWER,
        schema={"input_schema": {"type": "object", "properties": {}}},
    )
    def hpc_slurm_node_status(node: str = "", *, cluster: str = "default") -> str:
        ...
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hpc_pilot.rbac import Role

# ---------------------------------------------------------------------------
# Registry entry
# ---------------------------------------------------------------------------


@dataclass
class ToolEntry:
    """One entry in the canonical tool registry."""

    name: str
    fn: Callable[..., Any]
    role: Role
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any], Any], str] | None = None


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_TOOL_REGISTRY: dict[str, ToolEntry] = {}


# ---------------------------------------------------------------------------
# The decorator
# ---------------------------------------------------------------------------


def hpc_tool(
    name: str,
    role: Role | str,
    *,
    schema: dict[str, Any] | None = None,
    handler: Callable[[dict[str, Any], Any], str] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register an HPC-Pilot tool function.

    Args:
        name: Tool name (e.g. ``hpc_slurm_node_status``).
        role: Minimum RBAC role.
        schema: Anthropic-format schema dict.  If omitted, a minimal schema
            is built from the function signature.
        handler: Optional custom dispatch handler.  If omitted, a default
            handler is generated that passes ``cluster=args.get("cluster", "default")``
            and forwards all other args by name.

    The decorated function is returned unchanged; registration is a side
    effect.
    """
    resolved_role: Role = Role(role) if isinstance(role, str) else role

    # If no explicit schema, build a minimal one from the docstring and
    # function signature.
    actual_schema: dict[str, Any]
    if schema is not None:
        actual_schema = schema
    else:
        actual_schema = _build_schema_from_function(name, None)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        actual_handler = handler or _default_handler(name, fn)

        _TOOL_REGISTRY[name] = ToolEntry(
            name=name,
            fn=fn,
            role=resolved_role,
            schema=actual_schema,
            handler=actual_handler,
        )

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# Default dispatch handler builder
# ---------------------------------------------------------------------------


def _default_handler(name: str, fn: Callable[..., Any]) -> Callable[[dict[str, Any], Any], str]:
    """Build a dispatch handler that forwards args from the LLM to *fn*.

    Examines *fn*'s signature: parameters with defaults become optional
    (``args.get(...)``) and ones without become required (``args[...]``).
    Special parameters ``cluster``, ``dry_run`` get the convenience helpers.
    """
    import inspect

    sig = inspect.signature(fn)

    def _handler(args: dict[str, Any], tools: Any) -> str:
        kwargs: dict[str, Any] = {}

        for p_name, param in sig.parameters.items():
            # Skip variadic args (*args, **kwargs), not parameters literally named "args"
            if (p_name == "args" and param.kind == inspect.Parameter.VAR_POSITIONAL) or \
               (p_name == "kwargs" and param.kind == inspect.Parameter.VAR_KEYWORD) or \
               p_name.startswith("*"):
                continue

            # cluster
            if p_name == "cluster":
                kwargs[p_name] = args.get("cluster", "default")
                continue
            # dry_run — default to True for safety (many tools are destructive).
            if p_name == "dry_run":
                kwargs[p_name] = bool(args.get("dry_run", True))
                continue

            # actor / role  (used by hpc_slurm_job_cancel-style tools)
            if p_name in ("actor", "role"):
                # These are injected by invoke(), not from args dict
                continue

            # Everything else
            if param.default is not inspect.Parameter.empty:
                # Optional parameter — use .get()
                kwargs[p_name] = args.get(p_name, param.default)
            else:
                # Required parameter — use []
                kwargs[p_name] = args[p_name]

        result = fn(**kwargs)

        if isinstance(result, dict):
            return json.dumps(result, indent=2, default=str)
        return str(result)

    # Attach name for debugging
    _handler.__name__ = f"_dispatch_{name}"  # type: ignore[attr-defined]
    return _handler


# ---------------------------------------------------------------------------
# Schema builder (from function signature + docstring)
# ---------------------------------------------------------------------------


def _build_schema_from_function(name: str, fn: Callable[..., Any] | None) -> dict[str, Any]:
    """Build a minimal Anthropic-format schema from *fn*'s signature."""
    if fn is None:
        return {
            "name": name,
            "description": "",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        }

    import inspect

    sig = inspect.signature(fn)
    name_lower = name.lower()
    doc = inspect.getdoc(fn) or ""

    properties: dict[str, Any] = {}
    required: list[str] = []

    for p_name, param in sig.parameters.items():
        if p_name in ("args", "kwargs") or p_name.startswith("*"):
            continue

        if p_name in ("cluster", "dry_run", "actor", "role"):
            # These are internal, not LLM-facing
            continue

        # Infer type
        json_type: str = "string"
        if param.annotation is not inspect.Parameter.empty:
            ann = str(param.annotation)
            if "int" in ann:
                json_type = "integer"
            elif "bool" in ann:
                json_type = "boolean"
            elif "float" in ann:
                json_type = "number"

        description = f"Parameter ``{p_name}``"

        properties[p_name] = {
            "type": json_type,
            "description": description,
        }

        if param.default is inspect.Parameter.empty:
            required.append(p_name)

    return {
        "name": name,
        "description": (
            doc[:200] if doc else name_lower.replace("_", " ").replace("hpc ", "").capitalize()
        ),
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


# ---------------------------------------------------------------------------
# Derived views (legacy interface for consumers)
# ---------------------------------------------------------------------------


def get_registry() -> dict[str, ToolEntry]:
    """Return the full canonical registry (internal use)."""
    return _TOOL_REGISTRY


def get_tool_schemas() -> list[dict[str, Any]]:
    """Return a list of Anthropic-format schema dicts (replaces ``TOOL_SCHEMAS``)."""
    return [
        {
            "name": entry.name,
            "description": entry.schema.get("description", ""),
            "input_schema": entry.schema.get("input_schema", {"type": "object", "properties": {}}),
        }
        for entry in _TOOL_REGISTRY.values()
    ]


def get_dispatch() -> dict[str, Callable[[dict[str, Any], Any], str]]:
    """Return the dispatch dict (replaces ``_DISPATCH``)."""
    return {
        name: entry.handler for name, entry in _TOOL_REGISTRY.items() if entry.handler is not None
    }


def get_tool_min_role() -> dict[str, Role]:
    """Return the RBAC role dict (replaces ``TOOL_MIN_ROLE``)."""
    return {name: entry.role for name, entry in _TOOL_REGISTRY.items()}


# ---------------------------------------------------------------------------
# Backward-compatible module-level constants  (keep at bottom)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = []
_DISPATCH: dict[str, Callable[[dict[str, Any], Any], str]] = {}
TOOL_MIN_ROLE: dict[str, Role] = {}


def _rebuild_views() -> None:
    """Refresh the module-level backward-compat constants."""
    global TOOL_SCHEMAS, _DISPATCH, TOOL_MIN_ROLE  # noqa: PLW0603
    TOOL_SCHEMAS = get_tool_schemas()
    _DISPATCH = get_dispatch()
    TOOL_MIN_ROLE = get_tool_min_role()
