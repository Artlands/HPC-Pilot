"""
Hermes toolset for HPC cluster management.

This module provides Hermes-compatible tools for managing Slurm, Warewulf, Ansible, and Spack.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

# Import registry from Hermes tools
try:
    from tools.registry import registry
except ImportError:
    # Fallback for development
    registry = None


def check_slurm_available() -> bool:
    """Check if Slurm is installed and accessible."""
    try:
        subprocess.run(["scontrol", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_warewulf_available() -> bool:
    """Check if Warewulf is installed and accessible."""
    try:
        subprocess.run(["wwctl", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_spack_available() -> bool:
    """Check if Spack is installed and accessible."""
    try:
        subprocess.run(["spack", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def check_ansible_available() -> bool:
    """Check if Ansible is installed and accessible."""
    try:
        subprocess.run(["ansible", "--version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def hpc_slurm_node_status(node: str) -> str:
    """Get detailed status for a Slurm node.
    
    Args:
        node: Name of the node to query
        
    Returns:
        Node status information from scontrol
    """
    result = subprocess.run(
        ["scontrol", "show", "node", node],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def hpc_slurm_queue(filters: dict | None = None) -> str:
    """Get Slurm queue status with optional filters.
    
    Args:
        filters: Optional filter dictionary (e.g., {"user": "alice", "partition": "gpu"})
        
    Returns:
        Queue status output from squeue
    """
    cmd = ["squeue"]
    if filters:
        for key, value in filters.items():
            cmd.extend([f"--{key.replace('_', '-')}", str(value)])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout


def hpc_slurm_node_state(node: str, target: str, reason: str | None = None) -> str:
    """Change node state (drain, resume, down, undrain).
    
    Args:
        node: Node name
        target: Target state (drain, resume, down, undrain)
        reason: Reason for the state change (optional)
        
    Returns:
        Command output
    """
    cmd = ["scontrol", "update", "node=" + node, f"state={target}"]
    if reason:
        cmd.extend([f"reason={reason}"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout


def hpc_slurm_qos_modify(name: str, max_wall_min: int | None = None) -> str:
    """Modify Slurm QOS settings.
    
    Args:
        name: QOS name
        max_wall_min: Maximum wall time in minutes
        
    Returns:
        Command output
    """
    cmd = ["sacctmgr", "modify", "qos", name]
    if max_wall_min is not None:
        cmd.extend([f"MaxWall={max_wall_min}"])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return result.stdout


def hpc_warewulf_node_status() -> str:
    """Get Warewulf node status.
    
    Returns:
        Node status from wwctl node list
    """
    result = subprocess.run(
        ["wwctl", "node", "list"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def hpc_warewulf_image_list() -> str:
    """List Warewulf images.
    
    Returns:
        Image list from wwctl image list
    """
    result = subprocess.run(
        ["wwctl", "image", "list"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def hpc_warewulf_bootstrap(node: str) -> str:
    """Bootstrap a Warewulf node.
    
    Args:
        node: Node name to bootstrap
        
    Returns:
        Bootstrap command output
    """
    result = subprocess.run(
        ["wwctl", "node", "bootstrap", node],
        capture_output=True, text=True, timeout=300
    )
    return result.stdout


def hpc_spack_env_list() -> str:
    """List Spack environments.
    
    Returns:
        Environment list from spack env list
    """
    result = subprocess.run(
        ["spack", "env", "list"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def hpc_spack_find(env: str) -> str:
    """List installed specs in a Spack environment.
    
    Args:
        env: Environment name
        
    Returns:
        Installed specs from spack find
    """
    result = subprocess.run(
        ["spack", "find", "-l", "-N", "-d", "-e", env],
        capture_output=True, text=True, timeout=60
    )
    return result.stdout


def hpc_spack_compilers() -> str:
    """List available Spack compilers.
    
    Returns:
        Compiler list from spack compilers
    """
    result = subprocess.run(
        ["spack", "compilers"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def hpc_ansible_playbook_run(playbook: str, limit: str | None = None) -> str:
    """Run an Ansible playbook.
    
    Args:
        playbook: Path to the playbook
        limit: Host limit pattern (optional)
        
    Returns:
        Playbook execution output
    """
    cmd = ["ansible-playbook", playbook]
    if limit:
        cmd.extend(["--limit", limit])
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return result.stdout


def hpc_ansible_inventory_generate() -> str:
    """Generate Ansible inventory from cluster state.
    
    Returns:
        Generated inventory
    """
    result = subprocess.run(
        ["ansible-inventory", "-i", "localhost,", "--list"],
        capture_output=True, text=True, timeout=30
    )
    return result.stdout


def hpc_cluster_health_check() -> dict[str, Any]:
    """Run a comprehensive cluster health check.
    
    Returns:
        Health status dictionary
    """
    health: dict[str, Any] = {
        "timestamp": str(__import__('datetime').datetime.now()),
        "components": {},
        "overall": "healthy",
        "issues": [],
    }
    
    # Check Slurm
    slurm_available = check_slurm_available()
    health["components"]["slurm"] = {
        "available": slurm_available,
        "status": "unknown" if not slurm_available else "checking",
    }
    
    if slurm_available:
        try:
            result = subprocess.run(
                ["scontrol", "show", "nodes"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                nodes_status = parse_slurm_nodes(result.stdout)
                health["components"]["slurm"]["nodes"] = len(nodes_status)
                health["components"]["slurm"]["status"] = "healthy"
                
                # Check for down nodes
                down_nodes = [
                    name for name, info in nodes_status.items()
                    if info.get("NodeState") == "DOWN"
                ]
                if down_nodes:
                    health["components"]["slurm"]["status"] = "degraded"
                    health["issues"].append(f"Down nodes: {', '.join(down_nodes)}")
                    health["overall"] = "degraded"
        except Exception as e:
            health["components"]["slurm"]["status"] = "error"
            health["issues"].append(f"Slurm check error: {str(e)}")
            health["overall"] = "degraded"
    
    # Check Warewulf
    ww_available = check_warewulf_available()
    health["components"]["warewulf"] = {
        "available": ww_available,
        "status": "unknown" if not ww_available else "checking",
    }
    
    if ww_available:
        try:
            result = subprocess.run(
                ["wwctl", "node", "list"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                health["components"]["warewulf"]["status"] = "healthy"
        except Exception as e:
            health["components"]["warewulf"]["status"] = "error"
            health["issues"].append(f"Warewulf check error: {str(e)}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"
    
    # Check Spack
    spack_available = check_spack_available()
    health["components"]["spack"] = {
        "available": spack_available,
        "status": "unknown" if not spack_available else "checking",
    }
    
    if spack_available:
        try:
            result = subprocess.run(
                ["spack", "env", "list"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                health["components"]["spack"]["status"] = "healthy"
        except Exception as e:
            health["components"]["spack"]["status"] = "error"
            health["issues"].append(f"Spack check error: {str(e)}")
            if health["overall"] == "healthy":
                health["overall"] = "degraded"
    
    # Check Ansible
    ansible_available = check_ansible_available()
    health["components"]["ansible"] = {
        "available": ansible_available,
        "status": "unknown" if not ansible_available else "checking",
    }
    
    return health


def parse_slurm_nodes(output: str) -> dict[str, Any]:
    """Parse scontrol show nodes output into structured data."""
    nodes: dict[str, Any] = {}
    current_node: dict[str, Any] = {}
    
    for line in output.splitlines():
        line = line.strip()
        if not line:
            if current_node and "NodeName" in current_node:
                nodes[current_node["NodeName"]] = current_node
                current_node = {}
            continue
        
        if "=" in line:
            key, value = line.split("=", 1)
            current_node[key] = value
    
    if current_node and "NodeName" in current_node:
        nodes[current_node["NodeName"]] = current_node
    
    return nodes


# Register tools with Hermes registry if available
if registry is not None:
    registry.register(
        name="hpc_slurm_node_status",
        toolset="hpc",
        schema={
            "name": "hpc_slurm_node_status",
            "description": "Get detailed status for a Slurm node",
            "parameters": {
                "type": "object",
                "properties": {"node": {"type": "string"}},
                "required": ["node"]
            }
        },
        handler=lambda args, **kw: hpc_slurm_node_status(args.get("node", "")),
        check_fn=check_slurm_available,
        requires_env=[],
    )
    
    registry.register(
        name="hpc_slurm_queue",
        toolset="hpc",
        schema={
            "name": "hpc_slurm_queue",
            "description": "Get Slurm queue status with optional filters",
            "parameters": {
                "type": "object",
                "properties": {
                    "filters": {"type": "object", "default": {}}
                }
            }
        },
        handler=lambda args, **kw: hpc_slurm_queue(args.get("filters")),
        check_fn=check_slurm_available,
    )
    
    registry.register(
        name="hpc_slurm_node_state",
        toolset="hpc",
        schema={
            "name": "hpc_slurm_node_state",
            "description": "Change Slurm node state (drain, resume, down, undrain)",
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string"},
                    "target": {"type": "string", "enum": ["drain", "resume", "down", "undrain"]},
                    "reason": {"type": "string", "default": ""}
                },
                "required": ["node", "target"]
            }
        },
        handler=lambda args, **kw: hpc_slurm_node_state(
            args.get("node", ""),
            args.get("target", ""),
            args.get("reason")
        ),
        check_fn=check_slurm_available,
    )
    
    registry.register(
        name="hpc_slurm_qos_modify",
        toolset="hpc",
        schema={
            "name": "hpc_slurm_qos_modify",
            "description": "Modify Slurm QOS settings",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "max_wall_min": {"type": "integer", "default": None}
                },
                "required": ["name"]
            }
        },
        handler=lambda args, **kw: hpc_slurm_qos_modify(
            args.get("name", ""),
            args.get("max_wall_min")
        ),
        check_fn=check_slurm_available,
    )
    
    registry.register(
        name="hpc_warewulf_node_status",
        toolset="hpc",
        schema={
            "name": "hpc_warewulf_node_status",
            "description": "Get Warewulf node status",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        },
        handler=lambda args, **kw: hpc_warewulf_node_status(),
        check_fn=check_warewulf_available,
    )
    
    registry.register(
        name="hpc_warewulf_image_list",
        toolset="hpc",
        schema={
            "name": "hpc_warewulf_image_list",
            "description": "List Warewulf images",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        },
        handler=lambda args, **kw: hpc_warewulf_image_list(),
        check_fn=check_warewulf_available,
    )
    
    registry.register(
        name="hpc_warewulf_bootstrap",
        toolset="hpc",
        schema={
            "name": "hpc_warewulf_bootstrap",
            "description": "Bootstrap a Warewulf node",
            "parameters": {
                "type": "object",
                "properties": {"node": {"type": "string"}},
                "required": ["node"]
            }
        },
        handler=lambda args, **kw: hpc_warewulf_bootstrap(args.get("node", "")),
        check_fn=check_warewulf_available,
    )
    
    registry.register(
        name="hpc_spack_env_list",
        toolset="hpc",
        schema={
            "name": "hpc_spack_env_list",
            "description": "List Spack environments",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        },
        handler=lambda args, **kw: hpc_spack_env_list(),
        check_fn=check_spack_available,
    )
    
    registry.register(
        name="hpc_spack_find",
        toolset="hpc",
        schema={
            "name": "hpc_spack_find",
            "description": "List installed specs in a Spack environment",
            "parameters": {
                "type": "object",
                "properties": {"env": {"type": "string"}},
                "required": ["env"]
            }
        },
        handler=lambda args, **kw: hpc_spack_find(args.get("env", "")),
        check_fn=check_spack_available,
    )
    
    registry.register(
        name="hpc_spack_compilers",
        toolset="hpc",
        schema={
            "name": "hpc_spack_compilers",
            "description": "List available Spack compilers",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        },
        handler=lambda args, **kw: hpc_spack_compilers(),
        check_fn=check_spack_available,
    )
    
    registry.register(
        name="hpc_ansible_playbook_run",
        toolset="hpc",
        schema={
            "name": "hpc_ansible_playbook_run",
            "description": "Run an Ansible playbook",
            "parameters": {
                "type": "object",
                "properties": {
                    "playbook": {"type": "string"},
                    "limit": {"type": "string", "default": ""}
                },
                "required": ["playbook"]
            }
        },
        handler=lambda args, **kw: hpc_ansible_playbook_run(
            args.get("playbook", ""),
            args.get("limit")
        ),
        check_fn=check_ansible_available,
    )
    
    registry.register(
        name="hpc_ansible_inventory_generate",
        toolset="hpc",
        schema={
            "name": "hpc_ansible_inventory_generate",
            "description": "Generate Ansible inventory from cluster state",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        },
        handler=lambda args, **kw: hpc_ansible_inventory_generate(),
        check_fn=check_ansible_available,
    )
    
    registry.register(
        name="hpc_cluster_health_check",
        toolset="hpc",
        schema={
            "name": "hpc_cluster_health_check",
            "description": "Run a comprehensive cluster health check",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        },
        handler=lambda args, **kw: json.dumps(hpc_cluster_health_check()),
        check_fn=lambda: check_slurm_available() or check_warewulf_available(),
    )