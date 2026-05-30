"""Rolling image update across a group of nodes.

See spec 07 §5.
"""

from __future__ import annotations

from hpc_agent.core.ordering import topological_order
from hpc_agent.core.plan import Plan, Step
from hpc_agent.state.db import session_scope
from hpc_agent.state.repos import NodeRepo


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

    Returns:
        A Plan with dependency-ordered steps for rolling update
    """
    plan = Plan(
        id=f"rolling-update-{group}-{new_image}",
        intent=f"rolling update {group} to image {new_image}",
        actor=actor,
        steps=[],
        state="draft",
    )

    profile_name = profile or f"{group}-new"

    try:
        with session_scope() as session:
            nodes = NodeRepo(session).by_role(group)
            node_names = [n.hostname for n in nodes]
    except Exception:
        node_names = []

    if not node_names:
        raise ValueError(f"No nodes found in group '{group}'")

    _add_warewulf_steps(plan, node_names, new_image, profile_name)
    _add_ansible_steps(plan, node_names)
    _validate_and_order(plan.steps)

    return plan


def _add_warewulf_steps(
    plan: Plan, node_names: list[str], new_image: str, profile_name: str
) -> None:
    """Add Warewulf provisioning steps to the plan."""
    steps = plan.steps

    # First, ensure the new profile exists
    steps.append(
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

    # Batch processing: divide nodes into batches
    batches = [
        node_names[i : i + 2] for i in range(0, len(node_names), 2)
    ]

    for batch_idx, batch in enumerate(batches):
        batch_id = f"batch-{batch_idx + 1}"

        # Assign image to nodes in this batch
        steps.append(
            Step(
                id=f"{batch_id}-assign",
                tool="warewulf.assign_image_to_nodes",
                input={
                    "nodes": batch,
                    "profile": profile_name,
                },
                depends_on=["ensure-profile"] if batch_idx == 0 else [f"batch-{batch_idx}-assign"],
            )
        )

        # Rebuild overlay for this batch
        steps.append(
            Step(
                id=f"{batch_id}-rebuild",
                tool="warewulf.rebuild_overlay",
                input={
                    "node": ",".join(batch) if len(batch) > 1 else batch[0],
                },
                depends_on=[f"{batch_id}-assign"],
            )
        )


def _add_ansible_steps(plan: Plan, node_names: list[str]) -> None:
    """Add Ansible configuration steps for each node."""
    for node_idx, node in enumerate(node_names):
        node_id = node.replace("-", "_").replace(".", "_")

        # Apply common configuration
        plan.steps.append(
            Step(
                id=f"{node_id}-common",
                tool="ansible.run_playbook",
                input={
                    "playbook": "common",
                    "limit": node,
                    "extra_vars": {},
                },
                depends_on=[],
            )
        )

        # Apply chrony for time sync
        plan.steps.append(
            Step(
                id=f"{node_id}-chrony",
                tool="ansible.run_playbook",
                input={
                    "playbook": "chrony",
                    "limit": node,
                    "extra_vars": {},
                },
                depends_on=[f"{node_id}-common"],
            )
        )

        # Apply munge configuration
        plan.steps.append(
            Step(
                id=f"{node_id}-munge",
                tool="ansible.run_playbook",
                input={
                    "playbook": "munge",
                    "limit": node,
                    "extra_vars": {},
                },
                depends_on=[f"{node_id}-chrony"],
            )
        )

        # Apply slurm client configuration
        plan.steps.append(
            Step(
                id=f"{node_id}-slurm",
                tool="ansible.run_playbook",
                input={
                    "playbook": "slurm_client",
                    "limit": node,
                    "extra_vars": {},
                },
                depends_on=[f"{node_id}-munge"],
            )
        )

        # For GPU nodes, apply dcgm_exporter (will be skipped if not a GPU node)
        plan.steps.append(
            Step(
                id=f"{node_id}-dcgm",
                tool="ansible.run_playbook",
                input={
                    "playbook": "dcgm_exporter",
                    "limit": node,
                    "extra_vars": {},
                },
                depends_on=[f"{node_id}-slurm"],
            )
        )


def _validate_and_order(steps: list[Step]) -> None:
    """Validate dependencies and ensure topological order."""
    for step in steps:
        for dep in step.depends_on:
            if not any(s.id == dep for s in steps):
                raise ValueError(f"Unknown dependency '{dep}' in step '{step.id}'")

    topological_order(steps)
