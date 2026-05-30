"""Reconciliation between desired state and live state.

See spec 07 §6.
"""

from __future__ import annotations

from hpc_agent.core.plan import Plan, Step


def build(
    *,
    actor: str,
) -> Plan:
    """Create a plan to reconcile desired state with live cluster state.

    This workflow only performs READ operations to detect drift.
    """
    plan = Plan(
        id="reconcile-state",
        intent="reconcile desired state with live cluster state",
        actor=actor,
        steps=[],
        state="draft",
    )

    # All READ operations - no approval needed
    plan.steps.extend(
        [
            Step(
                id="query-warewulf",
                tool="warewulf.list_images",
                input={},
                depends_on=[],
            ),
            Step(
                id="query-nodes",
                tool="warewulf.list_nodes",
                input={},
                depends_on=[],
            ),
            Step(
                id="query-slurm-nodes",
                tool="slurm.node_status",
                input={},
                depends_on=[],
            ),
            Step(
                id="query-assocs",
                tool="slurm.show_assoc",
                input={},
                depends_on=[],
            ),
            Step(
                id="query-spack-envs",
                tool="spack.list_envs",
                input={},
                depends_on=[],
            ),
            Step(
                id="query-spack-find",
                tool="spack.find",
                input={"env": ""},
                depends_on=[],
            ),
        ]
    )

    # None of these are critical - they're all READ operations
    for step in plan.steps:
        step.critical = False

    return plan
