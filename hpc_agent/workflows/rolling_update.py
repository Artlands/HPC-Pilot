"""Rolling image update across a group of nodes.

See spec 07 §5.
"""

from __future__ import annotations

from hpc_agent.core.plan import Plan, Step


def build(
    group: str,
    new_image: str,
    batch_size: int = 2,
    profile: str | None = None,
    *,
    actor: str,
) -> Plan:
    """Create a plan to update nodes in a group with a new image.

    Args:
        group: Node group name (e.g., "compute_gpu")
        new_image: New Warewulf image name
        batch_size: Number of nodes to update concurrently
        profile: Warewulf profile name
        actor: Operator identity
    """
    plan = Plan(
        id=f"rolling-update-{group}",
        intent=f"rollingly update {group} to image {new_image}",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Note: In a real implementation, we'd query the state store to get nodes in group
    # For now, we'll create placeholder steps

    # First, ensure the new profile exists
    profile_name = profile or f"{group}-new"
    plan.steps.append(
        Step(
            id="ensure-profile",
            tool="warewulf.define_profile",
            input={
                "name": profile_name,
                "image": new_image,
                "system_overlays": ["wwinit", "hosts", "ssh.host_keys"],
                "runtime_overlays": ["hosts", "ssh.authorized_keys", "munge", "slurm"],
            },
            depends_on=[],
        )
    )

    # For demonstration, we'll create a single batch - in reality this would iterate
    # through batches of nodes
    plan.steps.append(
        Step(
            id="batch-1",
            tool="warewulf.assign_image_to_nodes",
            input={
                "nodes": [],  # Placeholder - would be populated from group query
                "profile": profile_name,
            },
            depends_on=["ensure-profile"],
        )
    )

    plan.steps.append(
        Step(
            id="rebuild-batch-1",
            tool="warewulf.rebuild_overlay",
            input={"node": ""},  # Placeholder
            depends_on=["batch-1"],
        )
    )

    return plan
