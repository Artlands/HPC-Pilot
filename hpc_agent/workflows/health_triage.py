"""Health monitoring and triage.

See spec 07 §7.
"""

from __future__ import annotations

from hpc_agent.core.plan import Plan, Step


def build(
    *,
    actor: str,
) -> Plan:
    """Create a plan to monitor cluster health and identify issues.

    This workflow detects problems and proposes remediation plans.
    """
    plan = Plan(
        id="health-triage",
        intent="monitor cluster health and identify issues",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Read-only health checks
    plan.steps.extend(
        [
            Step(
                id="check-down-nodes",
                tool="slurm.node_status",
                input={},
                depends_on=[],
            ),
            Step(
                id="check-sdiag",
                tool="slurm.diag",
                input={},
                depends_on=[],
            ),
        ]
    )

    for step in plan.steps:
        step.critical = False

    return plan
