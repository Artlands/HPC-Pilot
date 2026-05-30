"""Node maintenance / patch / rebuild.

See spec 07 §4.
"""

from __future__ import annotations

from typing import Literal

from hpc_agent.core.plan import Plan, Step


def build(
    node: str,
    action: Literal["patch", "rebuild_image"],
    new_image: str | None = None,
    new_profile: str | None = None,
    *,
    actor: str,
) -> Plan:
    """Create a plan to maintain a node.

    Args:
        node: Node hostname
        action: Action to perform (patch or rebuild_image)
        new_image: New Warewulf image for rebuild
        new_profile: New Warewulf profile for rebuild
        actor: Operator identity
    """
    plan = Plan(
        id=f"node-maint-{node}",
        intent=f"perform {action} on node {node}",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Step 1: Create maintenance reservation or drain
    plan.steps.append(
        Step(
            id="reserve-or-drain",
            tool="slurm.manage_reservation",
            input={
                "name": f"maint-{node}",
                "op": "create",
                "nodes": [node],
                "start": None,  # Immediately
                "duration_min": 120,
            },
            depends_on=[],
        )
    )

    # Step 2: Drain the node
    plan.steps.append(
        Step(
            id="drain-node",
            tool="slurm.node_state",
            input={
                "node": node,
                "target": "drain",
                "reason": f"maintenance: {action}",
            },
            depends_on=["reserve-or-drain"],
        )
    )

    # Step 3: Wait for node to be idle
    plan.steps.append(
        Step(
            id="wait-idle",
            tool="slurm.queue",
            input={"node": node},
            depends_on=["drain-node"],
        )
    )

    # Step 4: Perform action
    if action == "patch":
        plan.steps.append(
            Step(
                id="apply-patch",
                tool="ansible.run_playbook",
                input={
                    "playbook": "patch_system",
                    "limit": node,
                    "extra_vars": {},
                },
                depends_on=["wait-idle"],
            )
        )
    else:  # rebuild_image
        plan.steps.extend(
            [
                Step(
                    id="assign-new-image",
                    tool="warewulf.assign_image_to_nodes",
                    input={
                        "nodes": [node],
                        "profile": new_profile or f"{node}-new",
                    },
                    depends_on=["wait-idle"],
                ),
                Step(
                    id="rebuild-overlay",
                    tool="warewulf.rebuild_overlay",
                    input={"node": node},
                    depends_on=["assign-new-image"],
                ),
            ]
        )

    # Step 5: Re-validate
    plan.steps.append(
        Step(
            id="validate-after",
            tool="slurm.diag",
            input={},
            depends_on=["apply-patch"] if action == "patch" else ["rebuild-overlay"],
        )
    )

    # Step 6: Resume node
    plan.steps.append(
        Step(
            id="resume-node",
            tool="slurm.node_state",
            input={
                "node": node,
                "target": "resume",
            },
            depends_on=["validate-after"],
        )
    )

    # Step 7: Delete reservation
    plan.steps.append(
        Step(
            id="delete-reservation",
            tool="slurm.manage_reservation",
            input={
                "name": f"maint-{node}",
                "op": "delete",
            },
            depends_on=["resume-node"],
        )
    )

    for step in plan.steps[1:]:  # All steps after initial reservation
        step.critical = True

    return plan
