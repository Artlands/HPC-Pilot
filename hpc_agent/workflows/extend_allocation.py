"""Extend allocation for users, accounts, or QOS.

See spec 07 §2.
"""

from __future__ import annotations

from typing import Any, Literal

from hpc_agent.core.plan import Plan, Step


def build(
    target: Literal["user", "account", "qos"],
    field: str,
    value: int | str,
    name: str | None = None,
    user: str | None = None,
    account: str | None = None,
    *,
    actor: str,
) -> Plan:
    """Create a plan to extend an allocation (wall time, TRES, etc.).

    Args:
        target: What to extend (user, account, or qos)
        field: Field to modify (e.g., max_wall_min, grp_tres)
        value: New value for the field
        name: Name of the QOS or user/account
        user: Username (for user associations)
        account: Account name (for account associations)
        actor: Operator identity
    """
    plan = Plan(
        id=f"extend-{target}-{name or user or account}",
        intent=f"extend {target} {name or user or account} {field} to {value}",
        actor=actor,
        steps=[],
        state="draft",
    )

    tool_name: str
    tool_input: dict[str, Any]

    # Step 1: Read current state
    if target == "qos":
        plan.steps.append(
            Step(
                id="read-qos",
                tool="slurm.show_assoc",
                input={"qos": name},
                depends_on=[],
            )
        )
        tool_name = "slurm.manage_qos"
        tool_input = {"name": name, "op": "modify"}
    elif target == "account":
        plan.steps.append(
            Step(
                id="read-account",
                tool="slurm.show_assoc",
                input={"account": name},
                depends_on=[],
            )
        )
        tool_name = "slurm.extend_account"
        tool_input = {"name": name, "op": "modify"}
    else:  # user
        plan.steps.append(
            Step(
                id="read-user",
                tool="slurm.show_assoc",
                input={"user": user},
                depends_on=[],
            )
        )
        tool_name = "slurm.manage_user_assoc"
        tool_input = {"user": user, "account": account, "op": "modify"}

    # Add the field-specific input
    tool_input[field] = value
    tool_input["dry_run"] = True

    depends_on_map: dict[str, list[str]] = {
        "qos": ["read-qos"],
        "account": ["read-account"],
        "user": ["read-user"],
    }
    plan.steps.append(
        Step(
            id=f"modify-{target}",
            tool=tool_name,
            input=tool_input,
            depends_on=depends_on_map[target],
        )
    )

    plan.steps[-1].critical = True

    return plan
