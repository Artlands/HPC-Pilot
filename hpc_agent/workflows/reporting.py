"""Usage and allocation reporting.

See spec 07 §8.
"""

from __future__ import annotations

from datetime import datetime

from hpc_agent.core.plan import Plan, Step


def build(
    start: datetime,
    end: datetime,
    user: str | None = None,
    account: str | None = None,
    *,
    actor: str,
) -> Plan:
    """Create a plan to generate usage reports.

    This workflow only performs READ operations.
    """
    plan = Plan(
        id=f"usage-report-{start.date()}-{end.date()}",
        intent=f"generate usage report from {start} to {end}",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Read-only reporting
    plan.steps.extend(
        [
            Step(
                id="job-accounting",
                tool="slurm.job_accounting",
                input={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "user": user,
                },
                depends_on=[],
            ),
            Step(
                id="usage-report",
                tool="slurm.usage_report",
                input={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "account": account,
                },
                depends_on=[],
            ),
        ]
    )

    for step in plan.steps:
        step.critical = False

    return plan
