"""Add a compute node (CPU or GPU) to the cluster end-to-end.

See spec 07 §3.
"""

from __future__ import annotations

from typing import Literal

from hpc_agent.core.plan import Plan, Step


def build(
    hostname: str,
    mac: str,
    ip: str,
    role: Literal["compute_cpu", "compute_gpu"],
    partition: str,
    image: str,
    profile: str | None = None,
    gres: str | None = None,
    features: list[str] | None = None,
    *,
    actor: str,
) -> Plan:
    """Create a plan to provision and onboard a new compute node.

    Args:
        hostname: Node hostname
        mac: MAC address for PXE
        ip: IP address for PXE
        role: Node role (compute_cpu or compute_gpu)
        partition: Partition to join
        image: Warewulf image name
        profile: Warewulf profile name (defaults to role-based name)
        gres: GRES specification (e.g., "gpu:a100:8")
        features: Node features (comma-separated)
        actor: Operator identity
    """
    profile_name = profile or ("gpu-default" if role == "compute_gpu" else "cpu-default")
    features_str = ",".join(features or [])

    plan = Plan(
        id=f"add-node-{hostname}",
        intent=f"add {role} node {hostname} to partition {partition}",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Step 1: Ensure profile exists
    plan.steps.append(
        Step(
            id="ensure-profile",
            tool="warewulf.define_profile",
            input={
                "name": profile_name,
                "image": image,
                "system_overlays": ["wwinit", "hosts", "ssh.host_keys"],
                "runtime_overlays": ["hosts", "ssh.authorized_keys", "munge", "slurm"],
            },
            depends_on=[],
        )
    )

    # Step 2: Provision the node (register for PXE)
    plan.steps.append(
        Step(
            id="provision-node",
            tool="warewulf.provision_node",
            input={
                "hostname": hostname,
                "mac": mac,
                "ip": ip,
                "netdev": "eth0",
                "profile": profile_name,
                "role": role,
            },
            depends_on=["ensure-profile"],
        )
    )

    # Step 3: Rebuild overlay for this node
    plan.steps.append(
        Step(
            id="rebuild-overlay",
            tool="warewulf.rebuild_overlay",
            input={"node": hostname},
            depends_on=["provision-node"],
        )
    )

    # Optional: Apply Ansible roles after node Boots
    ansible_roles = ["common", "chrony", "munge", "slurm_client"]
    if role == "compute_gpu":
        ansible_roles.append("dcgm_exporter")

    # Step 4: Apply common configuration
    plan.steps.append(
        Step(
            id="apply-common-config",
            tool="ansible.run_playbook",
            input={
                "playbook": "common_config",
                "limit": hostname,
                "extra_vars": {},
            },
            depends_on=["rebuild-overlay"],
        )
    )

    # Step 5: Validate node is up
    plan.steps.append(
        Step(
            id="validate-node",
            tool="warewulf.node_status",
            input={"node": hostname},
            depends_on=["apply-common-config"],
        )
    )

    # Step 6: Add to partition
    plan.steps.append(
        Step(
            id="add-to-partition",
            tool="slurm.add_node_to_partition",
            input={
                "node": hostname,
                "partition": partition,
                "features": features_str.split(",") if features_str else [],
                "gres": gres,
            },
            depends_on=["validate-node"],
        )
    )

    # Step 7: Resume node
    plan.steps.append(
        Step(
            id="resume-node",
            tool="slurm.node_state",
            input={
                "node": hostname,
                "target": "resume",
            },
            depends_on=["add-to-partition"],
        )
    )

    # Mark steps as critical
    for i in range(1, len(plan.steps)):  # All steps after initial setup
        plan.steps[i].critical = True

    return plan
