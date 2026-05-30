"""Offboard a user from the HPC cluster.

See spec 07 §9.
"""

from __future__ import annotations

from hpc_agent.core.plan import Plan, Step


def build(
    user: str,
    account: str | None = None,
    archive_usage: bool = True,
    *,
    actor: str,
) -> Plan:
    """Create a plan to offboard a user.

    Note: Permanent deletion is prohibited. Only removes associations and archives data.
    """
    plan = Plan(
        id=f"offboard-user-{user}",
        intent=f"offboard user {user}",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Step 1: Archive usage if requested
    if archive_usage:
        plan.steps.append(
            Step(
                id="archive-usage",
                tool="slurm.job_accounting",
                input={
                    "user": user,
                    "start": None,  # All time
                    "end": None,
                },
                depends_on=[],
            )
        )

    # Step 2: Remove user association (modify, not delete)
    plan.steps.append(
        Step(
            id="remove-user-assoc",
            tool="slurm.manage_user_assoc",
            input={
                "user": user,
                "account": account or "",
                "op": "modify",
                "qos_list": [],
                "qos_add": None,
            },
            depends_on=["archive-usage"] if archive_usage else [],
        )
    )

    # Step 3: Remove from accounts if specified
    if account:
        plan.steps.append(
            Step(
                id="remove-from-account",
                tool="slurm.manage_user_assoc",
                input={
                    "user": user,
                    "account": account,
                    "op": "modify",
                    "qos_list": [],
                },
                depends_on=["remove-user-assoc"],
            )
        )

    # Mark all steps as non-critical (reporting/revocation only)
    for step in plan.steps:
        step.critical = False

    return plan
