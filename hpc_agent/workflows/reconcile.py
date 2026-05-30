"""Reconciliation between desired state and live state.

See spec 07 §6.

This workflow queries both the state store (desired state) and live cluster state,
computes drift, and proposes corrective actions WITHOUT auto-applying them.
"""

from __future__ import annotations

from hpc_agent.core.plan import Plan, Step


def build(
    *,
    actor: str,
) -> Plan:
    """Create a plan to reconcile desired state with live cluster state.

    This workflow only performs READ operations to detect drift.
    It DO NOT auto-apply corrections - it only proposes them for human review.

    Args:
        actor: Operator identity

    Returns:
        A Plan with all READ operations to detect drift
    """
    plan = Plan(
        id="reconcile-state",
        intent="reconcile desired state with live cluster state",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Query state store (desired state) - these READ operations populate the state store
    state_store_queries = [
        Step(
            id="query-warewulf-images",
            tool="warewulf.list_images",
            input={},
            depends_on=[],
        ),
        Step(
            id="query-warewulf-nodes",
            tool="warewulf.list_nodes",
            input={},
            depends_on=[],
        ),
        Step(
            id="query-slurm-nodes",
            tool="slurm.node_status",
            input={"reconcile_state": True},
            depends_on=[],
        ),
        Step(
            id="query-slurm-assocs",
            tool="slurm.show_assoc",
            input={},
            depends_on=[],
        ),
        Step(
            id="query-slurm-qos",
            tool="slurm.manage_qos",
            input={"name": "", "op": "read", "dry_run": True},
            depends_on=[],
        ),
        Step(
            id="query-slurm-accounts",
            tool="slurm.extend_account",
            input={"name": "", "op": "read", "dry_run": True},
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

    plan.steps.extend(state_store_queries)

    # All steps are non-critical - they're all READ operations
    for step in plan.steps:
        step.critical = False

    # Add a summary step that reports drift
    plan.steps.append(
        Step(
            id="generate-drift-report",
            tool="spack.spec",
            input={"spec": ""},
            depends_on=[s.id for s in state_store_queries],
        )
    )

    return plan


def analyze_drift(plan: Plan) -> dict[str, object]:
    """Analyze the results of a reconciliation plan to compute drift.

    Args:
        plan: A completed reconciliation plan

    Returns:
        A drift report with:
        - nodes_missing: Nodes in live state but not in desired state
        - nodes_extra: Nodes in desired state but not in live state
        - state_mismatches: Nodes where state differs (e.g., DRAINED vs UP)
        - repo:Drift Report: Summary of detected drift

    Note: This is a placeholder implementation. In a real system, this would:
    1. Query the state store for desired state
    2. Query live cluster state from the tool results
    3. Compare and compute differences
    4. Return a structured drift report
    5. Optionally propose corrective plans
    """
    return {
        "status": "drift_analysis_complete",
        "nodes_missing": 0,
        "nodes_extra": 0,
        "state_mismatches": 0,
        "repo:Drift Report": "No drift detected or drift analysis not yet implemented",
    }
