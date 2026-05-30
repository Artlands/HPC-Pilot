"""The approval gate: combines RBAC, policy, blast-radius caps, and risk tiers into a
single Gate decision. See spec 01 §3.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from hpc_agent.config.settings import settings
from hpc_agent.exec.rbac import Role, authorize
from hpc_agent.safety.diff import Diff
from hpc_agent.safety.policy import Effect, PolicyEngine
from hpc_agent.tools.base import Risk, ToolMeta


class Gate(BaseModel):
    requires_approval: bool = False
    denied: bool = False
    reason: str | None = None
    approved: bool = False
    approver: str | None = None


def evaluate(
    meta: ToolMeta,
    input_ctx: dict[str, Any],
    diff: Diff,
    *,
    actor_role: Role,
    policy: PolicyEngine | None = None,
    op: str | None = None,
) -> Gate:
    # 1. RBAC
    if not authorize(actor_role, meta.capability):
        return Gate(denied=True, reason=f"role {actor_role.value} lacks {meta.capability}")

    # 2. Policy engine
    if policy is not None:
        decision = policy.evaluate(
            tool=meta.name,
            domain=meta.domain,
            risk=meta.risk.value,
            op=op or input_ctx.get("op"),
            input_ctx=input_ctx | {"blast_radius": diff.blast_radius},
        )
        if decision.effect == Effect.DENY:
            return Gate(denied=True, reason=decision.message or f"denied by {decision.rule_id}")
        if decision.effect == Effect.REQUIRE_APPROVAL:
            return Gate(requires_approval=True, reason=decision.message or decision.rule_id)
        if decision.effect == Effect.AUTO:
            # Explicit in-policy allowance still respects blast-radius cap below.
            if diff.blast_radius > settings.max_blast_radius_auto:
                return Gate(requires_approval=True, reason="blast radius exceeds auto cap")
            return Gate()

    # 3. Blast-radius cap
    if diff.blast_radius > settings.max_blast_radius_auto:
        return Gate(requires_approval=True, reason="blast radius exceeds auto cap")

    # 4. Risk tier default
    if meta.risk in (Risk.READ, Risk.LOW):
        return Gate()
    if meta.risk == Risk.MEDIUM:
        return Gate(requires_approval=True, reason="medium-risk action without policy auto-allow")
    return Gate(requires_approval=True, reason="high-risk action")  # HIGH
