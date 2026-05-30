"""Plan / Step models. See spec 02 §3.

A Plan is a DAG of Steps; the executor runs them in dependency order, honoring gates and
pausing for approval (resumable, spec 02 §5).
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from hpc_agent.tools.result import ToolResult


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class PlanState(str, Enum):
    DRAFT = "draft"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"


class Step(BaseModel):
    id: str
    tool: str
    input: dict[str, Any]
    depends_on: list[str] = Field(default_factory=list)
    critical: bool = True  # failure halts forward progress
    status: StepStatus = StepStatus.PENDING
    result: ToolResult | None = None
    diff_hash: str | None = None  # set when paused, validated on resume


class Plan(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))  # == agent_run_id
    intent: str
    actor: str
    steps: list[Step]
    state: PlanState = PlanState.DRAFT

    def step(self, step_id: str) -> Step:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def is_mutating(self) -> bool:
        """Whether any step is potentially mutating (non-read tool)."""
        return any(not s.tool.endswith(("query", "status", "queue")) for s in self.steps)
