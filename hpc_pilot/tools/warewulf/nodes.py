"""Warewulf node lifecycle tools — add, set, delete, show, status, bulk add."""

from __future__ import annotations

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate
from hpc_pilot.tools.warewulf.services import _parse_key_value_sections


@hpc_tool(
    name="hpc_warewulf_node_status",
    role=Role.VIEWER,
    schema={
        "name": "hpc_warewulf_node_status",
        "description": "List Warewulf-provisioned nodes with their assigned boot images.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
)
def hpc_warewulf_node_status(*, cluster: str = "default") -> str:
    """Return wwctl node list output."""
    cl = _resolve_cluster(cluster)
    return _run([cl.warewulf("wwctl"), "node", "list"], cluster=cl)


@hpc_tool(
    name="hpc_warewulf_node_show",
    role=Role.VIEWER,
    schema={
        "name": "hpc_warewulf_node_show",
        "description": "Show detailed Warewulf node configuration (wwctl node show).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Node name"}},
            "required": ["name"],
        },
    },
)
def hpc_warewulf_node_show(name: str, *, cluster: str = "default") -> list[dict[str, str]]:
    """Show detailed node configuration.

    ``wwctl node show <name>``, returns structured parsed output.
    """
    _validate(name, "node name")
    cl = _resolve_cluster(cluster)
    raw = _run([cl.warewulf("wwctl"), "node", "show", name], cluster=cl)
    return _parse_key_value_sections(raw)


@hpc_tool(
    name="hpc_warewulf_node_add",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_node_add",
        "description": "Add a new Warewulf node definition.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name"},
                "mac": {"type": "string", "description": "MAC address of the node"},
                "ipaddr": {"type": "string", "description": "IP address of the node"},
                "profile": {"type": "string", "description": "Profile to assign to the node"},
            },
            "required": ["name", "mac", "ipaddr"],
        },
    },
)
def hpc_warewulf_node_add(
    name: str,
    mac: str,
    ipaddr: str,
    profile: str | None = None,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Add a new Warewulf node definition.

    ``wwctl node add <name> --mac=<mac> --ipaddr=<ip> [--profile=<profile>]``
    """
    _validate(name, "node name")
    _validate(mac, "MAC address")
    _validate(ipaddr, "IP address")
    cl = _resolve_cluster(cluster)
    cmd = [
        cl.warewulf("wwctl"),
        "node",
        "add",
        name,
        f"--mac={mac}",
        f"--ipaddr={ipaddr}",
    ]
    if profile:
        _validate(profile, "profile name")
        cmd.append(f"--profile={profile}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_warewulf_node_set",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_node_set",
        "description": "Update a Warewulf node definition (wwctl node set). Pass any node property as a keyword argument (mac, ipaddr, profile, image, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name"},
                "mac": {"type": "string", "description": "MAC address"},
                "ipaddr": {"type": "string", "description": "IP address"},
                "profile": {"type": "string", "description": "Profile name"},
                "image": {"type": "string", "description": "Image name"},
            },
            "required": ["name"],
        },
    },
)
def hpc_warewulf_node_set(
    name: str, *, cluster: str = "default", dry_run: bool = False, **kwargs: str
) -> str:
    """Update a Warewulf node definition.

    ``wwctl node set <name> --<key>=<value> ...``

    Accepts keyword arguments for any node property (mac, ipaddr, profile, image, etc).
    """
    _validate(name, "node name")
    cl = _resolve_cluster(cluster)
    cmd = [cl.warewulf("wwctl"), "node", "set", name]
    for key, value in kwargs.items():
        cmd.append(f"--{key}={value}")
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_warewulf_node_add_bulk",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_node_add_bulk",
        "description": "Add multiple Warewulf node definitions at once. Each node requires name, mac, ipaddr; profile is optional. Use this instead of repeated single node_add calls when provisioning many nodes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Node name"},
                            "mac": {"type": "string", "description": "MAC address"},
                            "ipaddr": {"type": "string", "description": "IP address"},
                            "profile": {"type": "string", "description": "Optional profile name"},
                        },
                        "required": ["name", "mac", "ipaddr"],
                    },
                    "description": "List of node definitions to add",
                }
            },
            "required": ["nodes"],
        },
    },
)
def hpc_warewulf_node_add_bulk(
    nodes: list[dict[str, str]],
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Add multiple Warewulf node definitions in a single invocation.

    Each dict in *nodes* must have ``name``, ``mac``, ``ipaddr`` keys and may
    optionally have ``profile``.  Example::

        nodes=[
            {"name": "compute09", "mac": "00:11:22:aa:bb:01", "ipaddr": "10.0.0.9", "profile": "compute"},
            {"name": "compute10", "mac": "00:11:22:aa:bb:02", "ipaddr": "10.0.0.10", "profile": "compute"},
        ]

    Returns a header line per node followed by any failures.
    """
    if not nodes:
        raise ValueError("At least one node definition is required")

    lines: list[str] = []
    for ndef in nodes:
        name = ndef.get("name", "")
        mac = ndef.get("mac", "")
        ipaddr = ndef.get("ipaddr", "")
        _validate(name, "node name")
        _validate(mac, "MAC address")
        _validate(ipaddr, "IP address")

        cl = _resolve_cluster(cluster)
        cmd = [
            cl.warewulf("wwctl"),
            "node",
            "add",
            name,
            f"--mac={mac}",
            f"--ipaddr={ipaddr}",
        ]
        profile = ndef.get("profile")
        if profile:
            _validate(profile, "profile name")
            cmd.append(f"--profile={profile}")

        result = _run(cmd, cluster=cl, dry_run=dry_run)
        lines.append(f"{name}: {result.strip() or 'OK'}")

    return "\n".join(lines)


@hpc_tool(
    name="hpc_warewulf_node_delete",
    role=Role.ADMIN,
    schema={
        "name": "hpc_warewulf_node_delete",
        "description": "Remove a Warewulf node definition (wwctl node delete).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Node name to delete"}},
            "required": ["name"],
        },
    },
)
def hpc_warewulf_node_delete(name: str, *, cluster: str = "default", dry_run: bool = False) -> str:
    """Remove a Warewulf node definition.

    ``wwctl node delete <name>``
    """
    _validate(name, "node name")
    cl = _resolve_cluster(cluster)
    return _run(
        [cl.warewulf("wwctl"), "node", "delete", name],
        cluster=cl,
        dry_run=dry_run,
    )
