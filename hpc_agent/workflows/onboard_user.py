"""Onboard a new user to the HPC cluster.

See spec 07 §1.
"""

from __future__ import annotations

from hpc_agent.core.plan import Plan, Step


def build(
    user: str,
    account: str,
    qos_list: list[str],
    default_qos: str | None = None,
    fairshare: int | None = None,
    create_home: bool = True,
    quota_gb: int | None = None,
    *,
    actor: str,
) -> Plan:
    """Create a plan to onboard a new user to the HPC cluster.

    Args:
        user: Username to onboard
        account: Account name (will be created if missing)
        qos_list: List of QOS names the user should have access to
        default_qos: Default QOS for the user
        fairshare: Fairshare priority value
        create_home: Whether to create home directory
        quota_gb: Home directory quota in GB
        actor: Operator identity
    """
    plan = Plan(
        id=f"onboard-user-{user}",
        intent=f"onboard user {user} with access to account {account}",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Step 1: Check if user already exists (READ - auto-run)
    plan.steps.append(
        Step(
            id="check-existing",
            tool="slurm.show_assoc",
            input={"user": user},
            depends_on=[],
        )
    )

    # Step 2-3: Create/modify account and QOS (if needed)
    if account:
        # Check if account exists
        plan.steps.append(
            Step(
                id="check-account-exists",
                tool="slurm.show_assoc",
                input={"account": account},
                depends_on=["check-existing"],
            )
        )

        # Create account if it doesn't exist
        plan.steps.append(
            Step(
                id="create-account",
                tool="slurm.extend_account",
                input={
                    "name": account,
                    "op": "create",
                    "parent": None,
                    "organization": None,
                    "description": f"Account for user {user}",
                },
                depends_on=["check-account-exists"],
            )
        )

    # Ensure QOS exists
    for qos in qos_list:
        plan.steps.append(
            Step(
                id=f"qos-check-{qos}",
                tool="slurm.manage_qos",
                input={"name": qos, "op": "create", "dry_run": True},
                depends_on=["check-existing"],
            )
        )

    # Step 4: Create user association
    plan.steps.append(
        Step(
            id="create-user-assoc",
            tool="slurm.manage_user_assoc",
            input={
                "user": user,
                "account": account,
                "op": "create",
                "qos_list": qos_list,
                "default_qos": default_qos,
                "fairshare": fairshare,
            },
            depends_on=["create-account"] if account else ["check-account-exists"],
        )
    )

    # Step 5: Create home directory if requested
    if create_home and quota_gb:
        plan.steps.append(
            Step(
                id="create-home",
                tool="ansible.run_playbook",
                input={
                    "playbook": "user_home",
                    "extra_vars": {
                        "user": user,
                        "quota_gb": quota_gb,
                    },
                },
                depends_on=["create-user-assoc"],
            )
        )

    # Step 6: Verify
    plan.steps.append(
        Step(
            id="verify",
            tool="slurm.show_assoc",
            input={"user": user},
            depends_on=["create-home"] if create_home else ["create-user-assoc"],
        )
    )

    # Mark non-critical steps that can be skipped on failure
    plan.steps[1].critical = False  # Check existing user
    plan.steps[2].critical = False  # Check account
    for i in range(3, len(plan.steps) - 1):  # All mutating steps
        plan.steps[i].critical = True
    plan.steps[-1].critical = False  # Verify step

    return plan
