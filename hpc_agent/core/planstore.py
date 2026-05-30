"""Plan persistence for resumable plans. See spec 02 §5.

A pluggable store; the default is in-memory (tests/local). Production swaps in a DB-backed
store implementing the same interface.
"""

from __future__ import annotations

from typing import Protocol

from hpc_agent.core.plan import Plan


class PlanStore(Protocol):
    def save(self, plan: Plan) -> None: ...
    def load(self, plan_id: str) -> Plan | None: ...


class InMemoryPlanStore:
    def __init__(self) -> None:
        self._plans: dict[str, Plan] = {}

    def save(self, plan: Plan) -> None:
        self._plans[plan.id] = plan

    def load(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)


_store: PlanStore = InMemoryPlanStore()


def set_store(store: PlanStore) -> None:
    global _store
    _store = store


def get_store() -> PlanStore:
    return _store
