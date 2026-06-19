"""
HPC Pilot AI agent — Claude-powered tool-use loop for cluster management.

The agent maps every hpc_* tool function to an Anthropic tool schema and runs
the standard tool-use loop:  user → Claude → tool call → result → Claude → answer.

Usage (programmatic):
    from hpc_pilot.agent import HpcAgent
    agent = HpcAgent()
    text, history = agent.run_turn("Show cluster health", history=[])

Usage (streaming CLI):
    text, history = agent.run_turn(
        "Drain gpu01 for maintenance",
        history=[],
        on_text=lambda chunk: print(chunk, end="", flush=True),
        on_tool=lambda name, args: print(f"\n  [→ {name}]", flush=True),
    )
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role, get_role

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "hpc_slurm_node_status",
        "description": (
            "Show detailed Slurm node status (CPU, memory, state, running jobs). "
            "Leave node empty to show all nodes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Node name. Empty string = show all nodes.",
                }
            },
        },
    },
    {
        "name": "hpc_slurm_queue",
        "description": "Show the Slurm job queue, optionally filtered by user, partition or state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Filter by username"},
                "partition": {"type": "string", "description": "Filter by partition name"},
                "state": {
                    "type": "string",
                    "description": "Filter by job state, e.g. RUNNING, PENDING",
                },
            },
        },
    },
    {
        "name": "hpc_slurm_node_state",
        "description": (
            "Change a Slurm node's state. "
            "drain = prevent new jobs; undrain/resume = allow jobs again; down = mark failed. "
            "Always query current state before changing it. "
            "Use dry_run=true to preview without executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "target": {
                    "type": "string",
                    "enum": ["drain", "undrain", "resume", "down"],
                },
                "reason": {"type": "string", "description": "Reason for the state change"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview the command without executing (default: true)",
                },
            },
            "required": ["node", "target"],
        },
    },
    {
        "name": "hpc_slurm_qos_modify",
        "description": (
            "Modify a Slurm QOS (Quality of Service) setting. "
            "Use dry_run=true first to preview the sacctmgr command before applying."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "QOS name"},
                "max_wall_min": {
                    "type": "integer",
                    "description": "Maximum wall-clock time in minutes",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without executing (default: true)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_status",
        "description": "List Warewulf-provisioned nodes with their assigned boot images.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_warewulf_image_list",
        "description": "List available Warewulf container images.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_warewulf_power_reset",
        "description": (
            "Power-reset a Warewulf node so it PXE-boots from its assigned image. "
            "This is disruptive — use dry_run=true to preview first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without executing (default: true)",
                },
            },
            "required": ["node"],
        },
    },
    # ---- Phase 2: Warewulf bootstrap & node lifecycle ----
    {
        "name": "hpc_warewulf_image_import",
        "description": "Import a container image into Warewulf (wwctl image import).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Image name"},
                "source": {"type": "string", "description": "Source path/URL of the image"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name", "source"],
        },
    },
    {
        "name": "hpc_warewulf_image_build",
        "description": (
            "Build a Warewulf container image. Computes a spec_hash from the build "
            "parameters and caches results in ~/.hpc-pilot/warewulf/builds/."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Image name to build"},
                "base": {"type": "string", "description": "Base image name"},
                "exec_steps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Build execution steps/commands",
                },
                "gpu": {
                    "type": "boolean",
                    "description": "Include GPU support (default: false)",
                },
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name", "base"],
        },
    },
    {
        "name": "hpc_warewulf_image_delete",
        "description": "Delete a Warewulf image (wwctl image delete).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Image name to delete"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_show",
        "description": "Show detailed Warewulf node configuration (wwctl node show).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_add",
        "description": "Add a new Warewulf node definition.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name"},
                "mac": {"type": "string", "description": "MAC address of the node"},
                "ipaddr": {"type": "string", "description": "IP address of the node"},
                "profile": {"type": "string", "description": "Profile to assign to the node"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name", "mac", "ipaddr"],
        },
    },
    {
        "name": "hpc_warewulf_node_set",
        "description": (
            "Update a Warewulf node definition (wwctl node set). "
            "Pass any node property as a keyword argument (mac, ipaddr, profile, image, etc)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name"},
                "mac": {"type": "string", "description": "MAC address"},
                "ipaddr": {"type": "string", "description": "IP address"},
                "profile": {"type": "string", "description": "Profile name"},
                "image": {"type": "string", "description": "Image name"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_delete",
        "description": "Remove a Warewulf node definition (wwctl node delete).",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name to delete"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_profile_list",
        "description": "List Warewulf profiles (wwctl profile list).",
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_profile_set",
        "description": (
            "Update a Warewulf profile (wwctl profile set). "
            "Pass any profile property as a keyword argument."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Profile name"},
                "image": {"type": "string", "description": "Default image for nodes using this profile"},
                "network": {"type": "string", "description": "Network configuration"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_overlay_list",
        "description": "List Warewulf overlays (wwctl overlay list).",
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_overlay_edit",
        "description": (
            "Edit a file inside a Warewulf overlay. Writes content to the overlay staging "
            "directory, commits to git, and rebuilds the overlay. Returns status dict."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string", "description": "Overlay name"},
                "path": {"type": "string", "description": "File path within the overlay"},
                "content": {"type": "string", "description": "File content to write"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["overlay", "path", "content"],
        },
    },
    {
        "name": "hpc_warewulf_overlay_build",
        "description": "Build a Warewulf overlay (wwctl overlay build).",
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string", "description": "Overlay name"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["overlay"],
        },
    },
    {
        "name": "hpc_warewulf_overlay_revert",
        "description": (
            "Revert an overlay to a prior git commit and rebuild. "
            "The overlay must have git history (created by overlay_edit)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string", "description": "Overlay name"},
                "commit": {
                    "type": "string",
                    "description": "Git commit ref to revert to (default: HEAD)",
                },
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["overlay"],
        },
    },
    {
        "name": "hpc_warewulf_configure_dhcp",
        "description": (
            "Configure Warewulf DHCP. Reads managed warewulf.conf, applies "
            "updates, copies to /etc/warewulf/warewulf.conf atomically, "
            "then runs wwctl configure dhcp. Superadmin only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "range_start": {"type": "string", "description": "DHCP range start IP"},
                "range_end": {"type": "string", "description": "DHCP range end IP"},
                "template": {"type": "string", "description": "DHCP config template path"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_configure_tftp",
        "description": "Configure Warewulf TFTP (wwctl configure tftp). Superadmin only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_configure_nfs",
        "description": "Configure Warewulf NFS exports (wwctl configure nfs). Superadmin only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_server_status",
        "description": "Return Warewulf server status (wwctl server status + systemctl is-active warewulfd).",
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_power_status",
        "description": "Return power status of a Warewulf node (wwctl power status).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "cluster": {"type": "string"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "hpc_warewulf_power_on",
        "description": "Power on a Warewulf node (wwctl power on).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "hpc_warewulf_power_off",
        "description": "Power off a Warewulf node (wwctl power off).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "hpc_spack_env_list",
        "description": "List all Spack environments on the cluster.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_spack_find",
        "description": "List installed software packages inside a Spack environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "Spack environment name"}
            },
            "required": ["env"],
        },
    },
    {
        "name": "hpc_spack_compilers",
        "description": "List available compilers registered in Spack.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_ansible_playbook_run",
        "description": (
            "Run an Ansible playbook against cluster nodes. "
            "Pass check=true to do a Ansible dry-run (--check). "
            "Pass dry_run=true to preview the ansible-playbook command without executing at all."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "playbook": {
                    "type": "string",
                    "description": "Absolute path to the YAML playbook file",
                },
                "limit": {
                    "type": "string",
                    "description": "Ansible host limit pattern (e.g. 'gpu_nodes')",
                },
                "check": {
                    "type": "boolean",
                    "description": "Pass --check to ansible-playbook (no changes on hosts)",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview command without executing (default: true)",
                },
            },
            "required": ["playbook"],
        },
    },
    {
        "name": "hpc_ansible_inventory_generate",
        "description": "Generate and display the current Ansible inventory.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_ansible_playbook_check",
        "description": (
            "Run ansible-playbook --check --diff to preview changes without applying them. "
            "Returns per-host structured diff output (JSON). "
            "Use dry_run=true to preview the command without executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "playbook": {
                    "type": "string",
                    "description": "Absolute path to the YAML playbook file",
                },
                "limit": {
                    "type": "string",
                    "description": "Ansible host limit pattern (e.g. 'gpu_nodes')",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview the command without executing (default: true)",
                },
            },
            "required": ["playbook"],
        },
    },
    {
        "name": "hpc_ansible_playbook_list",
        "description": "List all Ansible playbooks in the cluster's playbook directory with metadata.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_ansible_role_list",
        "description": "List all Ansible role directories on the cluster.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_ansible_inventory_from_truth",
        "description": (
            "Build an Ansible inventory YAML from Warewulf and Slurm source of truth. "
            "Queries wwctl node list and scontrol show nodes, then writes a "
            "YAML inventory with groups for gpu_nodes, cpu_nodes, and partitions."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_ansible_drift_check",
        "description": (
            "Run curated drift-check playbooks to detect configuration drift. "
            "Checks available: slurm-config, chrony-sync, mount, kernel-version. "
            "Pass which='all' (default) to run all, or specify a single check (e.g. 'mount')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "which": {
                    "type": "string",
                    "description": "Which drift check to run ('all' or specific check name)",
                },
            },
        },
    },
    {
        "name": "hpc_ansible_vault_decrypt",
        "description": (
            "Decrypt and view an Ansible Vault file. "
            "Content is never logged to the audit trail. "
            "Use dry_run=true to preview the path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the encrypted Ansible vault file",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without executing (default: true)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "hpc_ansible_run_history",
        "description": "Show the history of past Ansible playbook runs from the run log.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_cluster_health_check",
        "description": (
            "Run a comprehensive health check across all installed cluster components "
            "(Slurm, Warewulf, Spack, Ansible). Reports status and any detected issues."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {
                    "type": "string",
                    "description": "Target cluster name (default: 'default')",
                },
            },
        },
    },
    {
        "name": "hpc_skill_describe",
        "description": "Return the YAML definition of a named runbook/skill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (e.g. 'drain-and-patch-node')",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_skill_run",
        "description": (
            "Execute a named runbook/skill with the given inputs. "
            "Returns a run record with step results and status. "
            "Use resume_run_id to continue a paused run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name"},
                "inputs": {
                    "type": "object",
                    "description": "Input key-value pairs required by the skill",
                },
                "cluster": {"type": "string", "description": "Target cluster (default: 'default')"},
                "resume_run_id": {
                    "type": "string",
                    "description": "Run ID of a paused skill run to resume",
                },
            },
            "required": ["name"],
        },
    },
    # ---- Phase 1: Slurm full coverage ----
    {
        "name": "hpc_slurm_job_status",
        "description": "Show detailed status for a single Slurm job (scontrol show job).",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Slurm job ID (e.g. '12345')"},
                "cluster": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "hpc_slurm_job_hold",
        "description": "Put a pending Slurm job on hold so it does not start.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Slurm job ID"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "hpc_slurm_job_release",
        "description": "Release a held Slurm job so it may be scheduled.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Slurm job ID"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "hpc_slurm_job_requeue",
        "description": "Requeue a running or failed Slurm job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Slurm job ID"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "hpc_slurm_job_cancel",
        "description": (
            "Cancel a Slurm job. Operators may only cancel jobs they own; "
            "admins may cancel any job."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Slurm job ID"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "hpc_slurm_reservation_list",
        "description": "List all active Slurm reservations.",
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    },
    {
        "name": "hpc_slurm_reservation_create",
        "description": (
            "Create a Slurm reservation for scheduled maintenance or events. "
            "Use dry_run=true to preview the scontrol command first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Reservation name"},
                "nodes": {"type": "string", "description": "Node list or range, e.g. node[01-04]"},
                "start": {"type": "string", "description": "Start time, e.g. 'now'"},
                "duration": {"type": "string", "description": "Duration, e.g. '4:00:00'"},
                "users": {"type": "string", "description": "Comma-separated allowed users"},
                "accounts": {"type": "string", "description": "Comma-separated allowed accounts"},
                "flags": {"type": "string", "description": "Flags, e.g. 'MAINT,IGNORE_JOBS'"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name", "nodes", "start", "duration"],
        },
    },
    {
        "name": "hpc_slurm_reservation_update",
        "description": "Update an existing Slurm reservation. Use dry_run=true to preview.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Reservation name"},
                "nodes": {"type": "string"},
                "start": {"type": "string"},
                "duration": {"type": "string"},
                "users": {"type": "string"},
                "flags": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_slurm_reservation_delete",
        "description": "Delete a Slurm reservation. Use dry_run=true to preview.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Reservation name"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_slurm_partition_list",
        "description": "List all Slurm partitions with their configuration.",
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    },
    {
        "name": "hpc_slurm_partition_update",
        "description": (
            "Update a Slurm partition setting (state, max time). "
            "Always use dry_run=true first — this is cluster-wide."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Partition name"},
                "state": {
                    "type": "string",
                    "enum": ["up", "down", "drain", "inactive"],
                    "description": "New partition state",
                },
                "max_time": {"type": "string", "description": "Max wall time, e.g. '7-00:00:00'"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_slurm_account_list",
        "description": "List Slurm accounting accounts.",
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    },
    {
        "name": "hpc_slurm_account_create",
        "description": "Create a Slurm accounting account (sacctmgr add account). Superadmin only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Account name"},
                "description": {"type": "string"},
                "organization": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_slurm_association_list",
        "description": "List Slurm user-account associations, optionally filtered.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "user": {"type": "string"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_slurm_association_create",
        "description": "Associate a user with a Slurm account (sacctmgr add user). Superadmin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "account": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["user", "account"],
        },
    },
    {
        "name": "hpc_slurm_qos_list",
        "description": "List all Slurm QOS entries with their limits.",
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    },
    {
        "name": "hpc_slurm_qos_create",
        "description": "Create a new Slurm QOS entry (sacctmgr add qos). Requires admin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "max_wall_min": {"type": "integer", "description": "Max wall time in minutes"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_slurm_fairshare",
        "description": "Show Slurm fairshare usage (sshare -Pl).",
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    },
    {
        "name": "hpc_slurm_accounting",
        "description": (
            "Query Slurm job accounting history (sacct). "
            "Filter by user, account, time range, or job state."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "account": {"type": "string"},
                "start": {"type": "string", "description": "Start time, e.g. '2026-06-01'"},
                "end": {"type": "string", "description": "End time"},
                "state": {"type": "string", "description": "State filter, e.g. 'FAILED,TIMEOUT'"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_slurm_usage_report",
        "description": "Generate a Slurm usage report (sreport). type: cluster, account, or user.",
        "input_schema": {
            "type": "object",
            "properties": {
                "report_type": {
                    "type": "string",
                    "enum": ["cluster", "account", "user"],
                    "description": "Type of usage report",
                },
                "start": {"type": "string", "description": "Start date, e.g. '2026-06-01'"},
                "end": {"type": "string", "description": "End date"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_slurm_sdiag",
        "description": (
            "Show Slurm scheduler diagnostics (sdiag): cycle times, backfill depth, DBD state. "
            "Use to diagnose scheduling slowness."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    },
    {
        "name": "hpc_slurm_reconfigure",
        "description": (
            "Signal the Slurm controller to reload its configuration (scontrol reconfigure). "
            "Requires superadmin. Use dry_run=true to preview."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_slurm_config_show",
        "description": "Show the active Slurm controller configuration (scontrol show config).",
        "input_schema": {
            "type": "object",
            "properties": {"cluster": {"type": "string"}},
        },
    },
    # ---- Phase 2: Warewulf bootstrap & node lifecycle ----
    {
        "name": "hpc_warewulf_image_import",
        "description": "Import a container image into Warewulf for node provisioning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Image name"},
                "source": {"type": "string", "description": "Container image source URI"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name", "source"],
        },
    },
    {
        "name": "hpc_warewulf_image_build",
        "description": "Build a Warewulf container image. Uses spec_hash caching — identical inputs skip rebuild.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Image name"},
                "base": {"type": "string", "description": "Base image name"},
                "exec_steps": {"type": "array", "items": {"type": "string"}, "description": "Shell commands to run inside the container"},
                "gpu": {"type": "boolean", "description": "Include GPU/CUDA steps"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_image_delete",
        "description": "Delete a Warewulf container image.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_show",
        "description": "Show detailed Warewulf node configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Node name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_add",
        "description": "Add a new node to Warewulf provisioning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "mac": {"type": "string", "description": "MAC address"},
                "ipaddr": {"type": "string", "description": "IP address"},
                "profile": {"type": "string", "description": "Warewulf profile to assign"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name", "mac", "ipaddr"],
        },
    },
    {
        "name": "hpc_warewulf_node_set",
        "description": "Update a Warewulf node's configuration properties.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_delete",
        "description": "Remove a node from Warewulf provisioning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_profile_list",
        "description": "List all Warewulf profiles.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_warewulf_profile_set",
        "description": "Update a Warewulf profile's configuration.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_overlay_list",
        "description": "List Warewulf overlays.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_warewulf_overlay_edit",
        "description": "Edit a file in a Warewulf overlay with git versioning and rebuild.",
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string", "description": "Overlay name"},
                "path": {"type": "string", "description": "File path within the overlay"},
                "content": {"type": "string", "description": "File content"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["overlay", "path", "content"],
        },
    },
    {
        "name": "hpc_warewulf_overlay_build",
        "description": "Build/rebuild a Warewulf overlay.",
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["overlay"],
        },
    },
    {
        "name": "hpc_warewulf_overlay_revert",
        "description": "Revert overlay files to a prior git commit and rebuild.",
        "input_schema": {
            "type": "object",
            "properties": {
                "overlay": {"type": "string"},
                "commit": {"type": "string", "description": "Git commit ref (default: HEAD)"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["overlay"],
        },
    },
    {
        "name": "hpc_warewulf_configure_dhcp",
        "description": "Configure Warewulf DHCP. Applies updates to managed warewulf.conf and runs wwctl configure dhcp.",
        "input_schema": {
            "type": "object",
            "properties": {
                "range_start": {"type": "string", "description": "DHCP range start"},
                "range_end": {"type": "string", "description": "DHCP range end"},
                "template": {"type": "string", "description": "DHCP template name"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_configure_tftp",
        "description": "Configure Warewulf TFTP service (wwctl configure tftp).",
        "input_schema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_configure_nfs",
        "description": "Configure Warewulf NFS exports (wwctl configure nfs).",
        "input_schema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_warewulf_server_status",
        "description": "Return Warewulf server status (wwctl server + systemctl).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_warewulf_power_status",
        "description": "Return power status of a Warewulf node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "hpc_warewulf_power_on",
        "description": "Power on a Warewulf node (IPMI).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["node"],
        },
    },
    {
        "name": "hpc_warewulf_power_off",
        "description": "Power off a Warewulf node (IPMI).",
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["node"],
        },
    },
    # ---- Phase 3: Spack package lifecycle ----
    {
        "name": "hpc_spack_env_create",
        "description": "Create a new Spack environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "manifest": {"type": "string", "description": "Optional path to a spack.yaml manifest"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_spack_env_delete",
        "description": "Delete a Spack environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_spack_env_concretize",
        "description": "Concretize a Spack environment and return the lockfile diff (added/removed/changed specs).",
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "Environment name"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["env"],
        },
    },
    {
        "name": "hpc_spack_env_install",
        "description": "Install a Spack environment asynchronously. Returns a run_id for status polling.",
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["env"],
        },
    },
    {
        "name": "hpc_spack_env_status",
        "description": "Show the specs installed in a Spack environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {"type": "string"},
                "cluster": {"type": "string"},
            },
            "required": ["env"],
        },
    },
    {
        "name": "hpc_spack_install_spec",
        "description": "Install a single Spack spec outside of an environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "Spack spec string (e.g. 'gcc@12')"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "hpc_spack_uninstall",
        "description": "Uninstall a Spack package. dry_run is mandatory by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": {"type": "string", "description": "Spack spec to uninstall"},
                "dependents": {"type": "boolean", "description": "Also remove dependents"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["spec"],
        },
    },
    {
        "name": "hpc_spack_mirror_list",
        "description": "List configured Spack mirrors.",
        "input_schema": {"type": "object", "properties": {"cluster": {"type": "string"}}},
    },
    {
        "name": "hpc_spack_mirror_add",
        "description": "Add a Spack mirror URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "url": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["name", "url"],
        },
    },
    {
        "name": "hpc_spack_buildcache_push",
        "description": "Push packages to a Spack build cache mirror.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mirror_name": {"type": "string"},
                "spec": {"type": "string", "description": "Optional spec to push"},
                "gpg_key": {"type": "string", "description": "GPG key fingerprint"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["mirror_name"],
        },
    },
    {
        "name": "hpc_spack_buildcache_update_index",
        "description": "Update the build cache index for a mirror.",
        "input_schema": {
            "type": "object",
            "properties": {
                "mirror_name": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
            "required": ["mirror_name"],
        },
    },
    {
        "name": "hpc_spack_module_refresh",
        "description": "Refresh Spack-generated LMOD module files.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_spack_compiler_find",
        "description": "Register compilers with Spack (spack compiler find).",
        "input_schema": {
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}, "description": "Paths to search for compilers"},
                "dry_run": {"type": "boolean"},
                "cluster": {"type": "string"},
            },
        },
    },
    {
        "name": "hpc_job_status",
        "description": "Check the status of a background job (Spack install, Ansible run).",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Job run ID"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "hpc_job_logs",
        "description": "View the last N lines of a background job's log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "tail": {"type": "integer", "description": "Number of lines to show (default: 200)"},
            },
            "required": ["run_id"],
        },
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are HPC Pilot, an AI assistant for managing HPC clusters.
You have tools for Slurm, Warewulf, Ansible, and Spack.

Operator : {actor}
Role     : {role}

Role permissions:
• viewer     — read-only queries (node status, queue, health, Spack, Warewulf images)
• operator   — viewer + drain/resume nodes, run skills
• admin      — operator + modify QOS, run Ansible playbooks, bootstrap Warewulf nodes
• superadmin — admin + Slurm reconfig, Warewulf bootstrap (DHCP/TFTP/NFS), accounting schema

Interaction guidelines:
1. When asked about cluster state, call the relevant tool immediately.
2. Before any mutating operation, first query the current state to explain what will change.
3. For mutations, start with dry_run=true to show the command; only set dry_run=false
   after the operator explicitly confirms.
4. If a tool raises a permission error, explain what role is required.
5. Format output as Markdown: tables for tabular data, code blocks for raw command output.
6. Be concise — administrators are busy.
"""

# ---------------------------------------------------------------------------
# HpcAgent
# ---------------------------------------------------------------------------


def _load_env() -> None:
    """Load ~/.hpc-pilot/.env into the environment (silent if dotenv not installed)."""
    try:
        from dotenv import load_dotenv

        env_file = os.path.join(get_home(), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file, override=False)
    except ImportError:
        pass


_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    "claude-opus-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}
_DEFAULT_CONTEXT_TOKENS = 200_000
_SUMMARIZE_THRESHOLD = 0.80  # summarize when history > 80% of model context


def _estimate_tokens(messages: list[Any]) -> int:
    """Rough token estimate: 4 chars ≈ 1 token."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text", "") or block.get("content", ""))) // 4
                else:
                    total += len(str(getattr(block, "text", "") or "")) // 4
    return total


class HpcAgent:
    """Claude-powered agent that drives HPC cluster tool calls."""

    def __init__(
        self,
        model: str | None = None,
        role: Role | None = None,
        actor: str | None = None,
        summarize: bool = True,
    ) -> None:
        _load_env()
        from anthropic import Anthropic  # imported lazily so tests can stub

        from hpc_pilot.config import load_config

        cfg = load_config()
        self.model = model or os.environ.get("HPC_PILOT_MODEL") or cfg.model
        self.role: Role = role if role is not None else get_role()
        self.actor: str = (
            actor or os.environ.get("HPC_PILOT_ACTOR") or os.environ.get("USER", "cli")
        )
        self.summarize = summarize
        self._client = Anthropic()

    # ------------------------------------------------------------------
    # System prompt (with prompt-caching header)
    # ------------------------------------------------------------------

    def _system_prompt_blocks(self) -> list[dict[str, Any]]:
        text = _SYSTEM_PROMPT.format(actor=self.actor, role=self.role.value)
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    # ------------------------------------------------------------------
    # Context budget management
    # ------------------------------------------------------------------

    def _context_limit(self) -> int:
        return _MODEL_CONTEXT_TOKENS.get(self.model, _DEFAULT_CONTEXT_TOKENS)

    def _maybe_summarize(self, messages: list[Any]) -> list[Any]:
        """Summarize the oldest half of history if we're near the context limit."""
        if not self.summarize:
            return messages

        limit = self._context_limit()
        estimated = _estimate_tokens(messages)
        if estimated < int(limit * _SUMMARIZE_THRESHOLD):
            return messages

        from hpc_pilot.audit import AuditEvent, log_audit

        half = len(messages) // 2
        to_summarize = messages[:half]
        to_keep = messages[half:]

        summary_prompt = (
            "Summarize the following HPC Pilot conversation history into 1-2 paragraphs "
            "that preserve tool calls, decisions, and cluster state changes. "
            "Be concise but complete.\n\n"
            + "\n".join(
                f"{m['role']}: "
                + (m["content"] if isinstance(m["content"], str) else "[tool messages]")
                for m in to_summarize
            )
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            summary_text = "".join(
                block.text
                for block in resp.content
                if getattr(block, "type", "") == "text"
            )
            summary_msg: dict[str, Any] = {
                "role": "user",
                "content": f"[Summary of earlier conversation:] {summary_text}",
            }
            log_audit(AuditEvent(
                tool="conversation_summarize",
                actor=self.actor,
                role=self.role.value,
                args={"messages_summarized": half, "estimated_tokens_before": estimated},
                dry_run=False,
            ))
            return [summary_msg] + to_keep
        except Exception:
            return messages  # summarization failure must not break the turn

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Dispatch to a real tool function by name (no RBAC/audit — call _execute_tool instead)."""
        from hpc_pilot import tools
        from hpc_pilot.dispatch import _dispatch
        return _dispatch(name, args, tools)

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute one tool call: RBAC-check → audit → dispatch → return string result."""
        from hpc_pilot.dispatch import invoke

        try:
            return invoke(
                name, args, role=self.role, actor=self.actor,
                dry_run=bool(args.get("dry_run", False)),
            )
        except RuntimeError as exc:
            return f"[Tool error] {exc}"
        except ValueError as exc:
            return f"[Input error] {exc}"

    # ------------------------------------------------------------------
    # Conversation turn
    # ------------------------------------------------------------------

    def _make_api_request(
        self,
        messages: list[Any],
        on_text: Callable[[str], None] | None,
    ) -> tuple[str, Any]:
        """Make one API call with retry on transient errors.

        Returns (response_text, message_object).  Retries up to 3 times on
        RateLimitError/APIConnectionError with 1s → 2s → 4s backoff.  Streaming
        retries only if no chunks have been emitted yet (to avoid duplicate output).
        """
        import anthropic

        transient = (anthropic.RateLimitError, anthropic.APIConnectionError)
        delay = 1.0
        for attempt in range(3):
            try:
                if on_text is not None:
                    chunks: list[str] = []
                    with self._client.messages.stream(
                        model=self.model,
                        max_tokens=8096,
                        system=cast(Any, self._system_prompt_blocks()),
                        tools=cast(Any, TOOL_SCHEMAS),
                        messages=cast(Any, messages),
                    ) as stream:
                        for chunk in stream.text_stream:
                            on_text(chunk)
                            chunks.append(chunk)
                        msg: Any = stream.get_final_message()
                    return "".join(chunks), msg
                else:
                    msg = self._client.messages.create(
                        model=self.model,
                        max_tokens=8096,
                        system=cast(Any, self._system_prompt_blocks()),
                        tools=cast(Any, TOOL_SCHEMAS),
                        messages=cast(Any, messages),
                    )
                    text = "".join(
                        block.text
                        for block in msg.content
                        if getattr(block, "type", "") == "text"
                    )
                    return text, msg
            except transient:
                if attempt == 2:
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")  # mypy

    def run_turn(
        self,
        user_message: str,
        history: list[dict[str, Any]],
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any]], None] | None = None,
        on_result: Callable[[str, str], None] | None = None,
        max_iterations: int = 25,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Run one conversation turn (may invoke multiple tool calls internally).

        Args:
            user_message: The user's latest message.
            history: Previous messages in Anthropic format.
            on_text: Optional callback invoked per streaming text chunk.
                     When provided, the underlying API call uses streaming.
            on_tool: Optional callback invoked before each tool call with
                     (tool_name, args).
            on_result: Optional callback invoked after each tool call with
                       (tool_name, result_string).
            max_iterations: Maximum number of API calls before breaking the loop.

        Returns:
            (response_text, updated_history)
        """
        from hpc_pilot.audit import log_llm_usage

        # The Anthropic SDK accepts list[dict] for messages; cast satisfies mypy.
        messages: list[Any] = list(history) + [{"role": "user", "content": user_message}]
        messages = self._maybe_summarize(messages)
        response_text = ""
        iterations = 0

        while iterations < max_iterations:
            response_text, response = self._make_api_request(messages, on_text)

            try:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    log_llm_usage(
                        actor=self.actor,
                        role=self.role.value,
                        model=self.model,
                        input_tokens=int(getattr(usage, "input_tokens", 0)),
                        output_tokens=int(getattr(usage, "output_tokens", 0)),
                    )
            except Exception:
                pass  # usage logging must never block the turn

            iterations += 1
            messages = messages + [{"role": "assistant", "content": response.content}]

            if response.stop_reason != "tool_use":
                break

            # Execute tool calls and feed results back
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                tool_name: str = block.name
                tool_input: dict[str, Any] = dict(block.input)
                if on_tool is not None:
                    on_tool(tool_name, tool_input)
                try:
                    result = self._execute_tool(tool_name, tool_input)
                except PermissionError as exc:
                    result = f"[Permission denied] {exc}"
                except Exception as exc:
                    result = f"[Unexpected error] {exc}"
                if on_result is not None:
                    on_result(tool_name, result)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

            messages = messages + [{"role": "user", "content": tool_results}]
            response_text = ""  # reset — we'll get new text in the next iteration
        else:
            response_text = (
                f"[Stopped after {max_iterations} iterations — "
                "possible infinite tool-call loop.]"
            )

        return response_text, messages

    def run_query(self, query: str) -> str:
        """Single-shot query with no conversation history."""
        text, _ = self.run_turn(query, [])
        return text


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


def _session_path(session_id: str) -> str:
    from hpc_pilot.paths import sessions_dir
    return os.path.join(sessions_dir(), f"{session_id}.json")


def _new_session_id() -> str:
    """Return a timestamp-based session ID that doesn't collide with existing files."""
    import datetime
    base = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if not os.path.exists(_session_path(base)):
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        if not os.path.exists(_session_path(candidate)):
            return candidate
    return base


def _serialize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert one Anthropic history message to a plain JSON-serializable dict.

    Assistant messages carry SDK content-block objects; this converts them to
    plain dicts so they survive a round-trip through json.dump / json.load.
    """
    content = msg.get("content")
    if not isinstance(content, list):
        return dict(msg)
    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        elif hasattr(block, "model_dump"):
            blocks.append(block.model_dump())
        elif hasattr(block, "dict"):
            blocks.append(block.dict())
        else:
            d: dict[str, Any] = {"type": getattr(block, "type", "unknown")}
            for attr in ("text", "id", "name", "input", "tool_use_id", "content"):
                val = getattr(block, attr, None)
                if val is not None:
                    d[attr] = val
            blocks.append(d)
    return {"role": msg["role"], "content": blocks}


def save_session(
    history: list[dict[str, Any]],
    agent: HpcAgent,
    session_id: str | None = None,
) -> str:
    """Persist *history* to ~/.hpc-pilot/sessions/<id>.json.

    Returns the session ID so callers can print a resume hint.
    """
    from hpc_pilot.paths import ensure_layout
    ensure_layout()
    sid = session_id or _new_session_id()
    record: dict[str, Any] = {
        "id": sid,
        "ts": time.time(),
        "model": agent.model,
        "role": agent.role.value,
        "actor": agent.actor,
        "messages": [_serialize_message(m) for m in history],
    }
    with open(_session_path(sid), "w") as f:
        json.dump(record, f, indent=2, default=str)
    return sid


def load_session(session_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load a saved session; return *(messages, metadata)*.

    Raises FileNotFoundError when the session does not exist.
    """
    path = _session_path(session_id)
    with open(path) as f:
        data: dict[str, Any] = json.load(f)
    messages: list[dict[str, Any]] = data.pop("messages", [])
    return messages, data


def list_sessions() -> list[dict[str, Any]]:
    """Return session summaries sorted newest-first.

    Each summary has keys: id, ts, model, role, actor, turn_count.
    """
    from hpc_pilot.paths import sessions_dir
    sdir = sessions_dir()
    if not os.path.isdir(sdir):
        return []
    summaries: list[dict[str, Any]] = []
    for fname in os.listdir(sdir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(sdir, fname)) as f:
                data = json.load(f)
            summaries.append({
                "id": data.get("id", fname[:-5]),
                "ts": float(data.get("ts", 0)),
                "model": str(data.get("model", "")),
                "role": str(data.get("role", "")),
                "actor": str(data.get("actor", "")),
                "turn_count": sum(
                    1 for m in data.get("messages", []) if m.get("role") == "user"
                ),
            })
        except Exception:
            continue
    summaries.sort(key=lambda s: s["ts"], reverse=True)
    return summaries


# ---------------------------------------------------------------------------
# Interactive CLI chat session
# ---------------------------------------------------------------------------


def run_chat_loop(agent: HpcAgent, initial_history: list[dict[str, Any]] | None = None) -> int:
    """Run an interactive readline-based chat loop in the terminal."""
    import contextlib
    with contextlib.suppress(ImportError):
        import readline  # noqa: F401  — enables Ctrl-A/E, history on supported platforms

    history: list[dict[str, Any]] = list(initial_history) if initial_history else []
    turn_start = len(history)  # messages present before this session's turns
    print(
        f"HPC Pilot AI  [model: {agent.model} | role: {agent.role.value}]"
        "\nType 'exit' or press Ctrl-D to quit.\n"
    )

    try:
        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if user_input.lower() in ("exit", "quit", "q"):
                break
            if not user_input:
                continue

            print("Agent: ", end="", flush=True)
            try:
                def _on_tool(name: str, args: dict[str, Any]) -> None:
                    arg_str = json.dumps(args, default=str)
                    if len(arg_str) > 80:
                        arg_str = arg_str[:77] + "..."
                    print(f"\n  [→ {name}] {arg_str}", end=" ", flush=True)

                def _on_result(_name: str, result: str) -> None:
                    snippet = result[:150] + ("…" if len(result) > 150 else "")
                    print(f"\n  [← {snippet}]", end=" ", flush=True)

                _, history = agent.run_turn(
                    user_input,
                    history,
                    on_text=lambda chunk: print(chunk, end="", flush=True),
                    on_tool=_on_tool,
                    on_result=_on_result,
                )
            except KeyboardInterrupt:
                print("\n(interrupted)")
            except Exception as exc:
                print(f"\nError: {exc}")
            print()
    finally:
        if len(history) > turn_start:
            try:
                sid = save_session(history, agent)
                print(f"\nSession saved: {sid}")
                print(f"  Resume with: hpc-pilot chat --resume {sid}")
            except Exception as exc:
                print(f"\n[Warning] Could not save session: {exc}")

    return 0
