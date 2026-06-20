"""Warewulf power management tools — reset, status, on, off."""

from __future__ import annotations

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate


@hpc_tool(
    name="hpc_warewulf_power_reset",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_power_reset",
        "description": "Power-reset a Warewulf node so it PXE-boots from its assigned image. This is disruptive — use dry_run=true to preview first.",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Node name"}},
            "required": ["node"],
        },
    },
)
def hpc_warewulf_power_reset(node: str, dry_run: bool = False, *, cluster: str = "default") -> str:
    """Power-reset a Warewulf node so it PXE-boots from its assigned image."""
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "reset", node],
        cluster=cl,
        timeout=120,
        dry_run=dry_run,
    )


@hpc_tool(
    name="hpc_warewulf_power_status",
    role=Role.VIEWER,
    schema={
        "name": "hpc_warewulf_power_status",
        "description": "Return power status of a Warewulf node (wwctl power status).",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Node name"}},
            "required": ["node"],
        },
    },
)
def hpc_warewulf_power_status(node: str, *, cluster: str = "default") -> str:
    """Return power status of a Warewulf node.

    ``wwctl power status <node>``
    """
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "status", node],
        cluster=cl,
    )


@hpc_tool(
    name="hpc_warewulf_power_on",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_power_on",
        "description": "Power on a Warewulf node (wwctl power on).",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Node name"}},
            "required": ["node"],
        },
    },
)
def hpc_warewulf_power_on(node: str, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Power on a Warewulf node.

    ``wwctl power on <node>``
    """
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "on", node],
        cluster=cl,
        dry_run=dry_run,
    )


@hpc_tool(
    name="hpc_warewulf_power_off",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_power_off",
        "description": "Power off a Warewulf node (wwctl power off).",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Node name"}},
            "required": ["node"],
        },
    },
)
def hpc_warewulf_power_off(node: str, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Power off a Warewulf node.

    ``wwctl power off <node>``
    """
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "power", "off", node],
        cluster=cl,
        dry_run=dry_run,
    )
