"""Extend allocation for users, accounts, or QOS.

See spec 07 §2.
"""

from __future__ import annotations

from typing import Any, Literal

from typing import Any

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
    elif target == "account":
        plan.steps.append(
            Step(
                id="read-account",
                tool="slurm.show_assoc",
                input={"account": name},
                depends_on=[],
            )
        )
    else:  # user
        plan.steps.append(
            Step(
                id="read-user",
                tool="slurm.show_assoc",
                input={"user": user},
                depends_on=[],
            )
        )

    tool_name = "slurm.set_limits"

    # Build set_limits input
    tool_input: dict[str, Any] = {
        "target": target,
        field: value,
        "dry_run": True,
    }
    if target == "qos":
        tool_input["name"] = name
    elif target == "account":
        tool_input["name"] = name
    else:  # user
        tool_input["user"] = user
        tool_input["account"] = account

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
            depends_on=depends_on_map.get(target, []),
        )
    )

    plan.steps[-1].critical = True

    return plan
