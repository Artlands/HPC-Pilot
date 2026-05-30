"""Planner. See spec 02 §2-3.

The planner turns an intent into a Plan (a dependency-ordered set of typed tool calls).
This reference implementation is a deterministic, rule-based planner so the executor can
be exercised end-to-end without an LLM. The LLM planner (llm.py) will implement the same
`build_plan(intent, ...) -> Plan` signature, emitting the same Step structures.

The structured planner also exposes `plan_from_steps`, which any caller (LLM or workflow
in spec 07) uses to assemble a validated Plan from explicit steps.
"""

from __future__ import annotations

import re
from typing import Any

from hpc_agent.core.ordering import topological_order
from hpc_agent.core.plan import Plan, Step


def plan_from_steps(intent: str, actor: str, steps: list[Step]) -> Plan:
    """Assemble and validate a Plan (checks the dependency DAG)."""
    topological_order(steps)  # raises on cycle / unknown dep
    return Plan(intent=intent, actor=actor, steps=steps)


# --- minimal deterministic intent matching (placeholder for the LLM planner) ---

# A duration anywhere in the sentence: "48 hours", "2 days", "90 min".
_DURATION_RE = re.compile(
    r"(\d+)\s*(hours?|hrs?|h|days?|d|minutes?|mins?|m)\b",
    re.IGNORECASE,
)
# QOS name from either "<name> qos" or "qos <name>".
_QOS_BEFORE_RE = re.compile(r"\b(\w+)\s+qos\b", re.IGNORECASE)
_QOS_AFTER_RE = re.compile(r"\bqos\s+(\w+)\b", re.IGNORECASE)
# Signals this is a wall-time intent.
_WALL_SIGNAL_RE = re.compile(r"\b(wall|wall\s*time|time)\b", re.IGNORECASE)

_STOPWORDS = {"the", "a", "an", "this", "that"}


def _to_minutes(value: int, unit: str) -> int:
    u = unit.lower()
    if u.startswith("d"):
        return value * 1440
    if u.startswith("h"):
        return value * 60
    return value  # minutes


def _find_qos_name(text: str) -> str | None:
    for rx in (_QOS_BEFORE_RE, _QOS_AFTER_RE):
        m = rx.search(text)
        if m and m.group(1).lower() not in _STOPWORDS:
            return m.group(1)
    return None


def build_plan(intent: str, actor: str) -> Plan:
    """Build a Plan from a natural-language intent (rule-based reference)."""
    text = intent.strip()

    duration = _DURATION_RE.search(text)
    qos_name = _find_qos_name(text)
    if duration and qos_name and _WALL_SIGNAL_RE.search(text):
        minutes = _to_minutes(int(duration.group(1)), duration.group(2))
        step_input: dict[str, Any] = {
            "name": qos_name,
            "op": "modify",
            "max_wall_min": minutes,
        }
        step = Step(id="extend_wall", tool="slurm.manage_qos", input=step_input)
        return plan_from_steps(intent, actor, [step])

    raise ValueError(
        "rule-based planner could not parse this intent; "
        "the LLM planner (llm.py) handles open-ended intents"
    )
