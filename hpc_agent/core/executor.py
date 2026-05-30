"""Plan executor. See spec 02 §4 and §5.

Runs a Plan in dependency order. For each step: invoke the tool in dry-run to populate a
Diff, evaluate the safety gate, then either execute, pause for approval, or fail. Paused
plans persist and resume after approval (with diff-hash re-validation).
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel

from hpc_agent.core.ordering import topological_order
from hpc_agent.core.plan import Plan, PlanState, StepStatus
from hpc_agent.core.planstore import get_store
from hpc_agent.exec.rbac import Role
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.diff import Diff
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.base import get_tool
from hpc_agent.tools.result import ToolResult, ToolStatus


def _diff_hash(diff: Diff) -> str:
    return hashlib.sha256(diff.model_dump_json().encode()).hexdigest()


def _call_tool(
    tool_name: str,
    raw_input: dict[str, object],
    *,
    actor: str,
    actor_role: Role,
    policy: PolicyEngine | None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    meta, fn, _br = get_tool(tool_name)
    inp_model: BaseModel = meta.input_model.model_validate(raw_input)
    kwargs: dict[str, object] = {"actor": actor, "actor_role": actor_role, "policy": policy}
    if gate_override is not None:
        kwargs["gate_override"] = gate_override
    return fn(inp_model, **kwargs)


def run_plan(
    plan: Plan,
    *,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> Plan:
    """Execute (or resume) a plan. Stops and persists on the first step needing approval."""
    plan.state = PlanState.RUNNING
    failed_ids: set[str] = {s.id for s in plan.steps if s.status == StepStatus.FAILED}

    for step in topological_order(plan.steps):
        if step.status in (StepStatus.DONE, StepStatus.FAILED, StepStatus.SKIPPED):
            continue

        # skip if any dependency failed
        if any(dep in failed_ids for dep in step.depends_on):
            step.status = StepStatus.SKIPPED
            continue

        # 1. dry-run to populate the diff
        dry = _call_tool(
            step.tool,
            {**step.input, "dry_run": True},
            actor=plan.actor,
            actor_role=actor_role,
            policy=policy,
        )

        if dry.status == ToolStatus.DENIED:
            step.status = StepStatus.FAILED
            step.result = dry
            failed_ids.add(step.id)
            if step.critical:
                plan.state = PlanState.FAILED
                get_store().save(plan)
                return plan
            continue

        if dry.status == ToolStatus.ERROR:
            step.status = StepStatus.FAILED
            step.result = dry
            failed_ids.add(step.id)
            if step.critical:
                plan.state = PlanState.FAILED
                get_store().save(plan)
                return plan
            continue

        # If dry-run reported a no-op success, nothing to apply.
        if dry.status == ToolStatus.OK:
            step.status = StepStatus.DONE
            step.result = dry
            continue

        # dry.status == DRY_RUN here; evaluate the gate on the produced diff
        assert dry.diff is not None
        meta, _fn, _br = get_tool(step.tool)
        g = safety_gate.evaluate(
            meta,
            step.input,
            dry.diff,
            actor_role=actor_role,
            policy=policy,
            op=step.input.get("op"),
        )

        if g.denied:
            step.status = StepStatus.FAILED
            step.result = ToolResult.denied(g.reason or "denied")
            failed_ids.add(step.id)
            if step.critical:
                plan.state = PlanState.FAILED
                get_store().save(plan)
                return plan
            continue

        if g.requires_approval and not g.approved:
            step.status = StepStatus.NEEDS_APPROVAL
            step.diff_hash = _diff_hash(dry.diff)
            plan.state = PlanState.PAUSED
            get_store().save(plan)
            return plan  # resumable

        # 2. execute for real
        real = _call_tool(
            step.tool,
            {**step.input, "dry_run": False},
            actor=plan.actor,
            actor_role=actor_role,
            policy=policy,
            gate_override=g,
        )
        step.result = real
        step.status = StepStatus.DONE if real.ok else StepStatus.FAILED
        if not real.ok:
            failed_ids.add(step.id)
            if step.critical:
                plan.state = PlanState.FAILED
                get_store().save(plan)
                return plan

    plan.state = PlanState.DONE if not failed_ids else PlanState.FAILED
    get_store().save(plan)
    return plan


def resume_plan(
    plan_id: str,
    step_id: str,
    approver: str,
    *,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> Plan:
    """Approve a paused step and continue. Re-validates the diff hash (spec 02 §5)."""
    plan = get_store().load(plan_id)
    if plan is None:
        raise KeyError(f"unknown plan {plan_id}")
    step = plan.step(step_id)
    if step.status != StepStatus.NEEDS_APPROVAL:
        raise ValueError(f"step {step_id} is not awaiting approval (status={step.status})")

    # Re-run dry-run and confirm the diff hasn't changed since approval was requested.
    dry = _call_tool(
        step.tool,
        {**step.input, "dry_run": True},
        actor=plan.actor,
        actor_role=actor_role,
        policy=policy,
    )
    if dry.status != ToolStatus.DRY_RUN or dry.diff is None:
        # state converged or errored in the meantime
        step.status = StepStatus.DONE if dry.status == ToolStatus.OK else StepStatus.FAILED
        step.result = dry
        return run_plan(plan, actor_role=actor_role, policy=policy)

    if _diff_hash(dry.diff) != step.diff_hash:
        step.status = StepStatus.FAILED
        step.result = ToolResult.denied("approval invalidated: plan diff changed")
        plan.state = PlanState.FAILED
        get_store().save(plan)
        return plan

    # Apply with an approved gate override.
    approved_gate = safety_gate.Gate(requires_approval=True, approved=True, approver=approver)
    real = _call_tool(
        step.tool,
        {**step.input, "dry_run": False},
        actor=plan.actor,
        actor_role=actor_role,
        policy=policy,
        gate_override=approved_gate,
    )
    step.result = real
    step.status = StepStatus.DONE if real.ok else StepStatus.FAILED
    return run_plan(plan, actor_role=actor_role, policy=policy)
