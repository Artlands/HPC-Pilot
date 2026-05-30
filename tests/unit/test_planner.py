from __future__ import annotations

import pytest

from hpc_agent.core.ordering import CycleError, topological_order
from hpc_agent.core.plan import Step
from hpc_agent.core.planner import build_plan


def _step(sid: str, deps: list[str]) -> Step:
    return Step(id=sid, tool="slurm.manage_qos", input={}, depends_on=deps)


def test_topo_order_respects_deps() -> None:
    steps = [_step("c", ["a", "b"]), _step("a", []), _step("b", ["a"])]
    order = [s.id for s in topological_order(steps)]
    assert order.index("a") < order.index("b") < order.index("c")


def test_topo_unknown_dep_raises() -> None:
    with pytest.raises(KeyError):
        topological_order([_step("a", ["ghost"])])


def test_topo_cycle_raises() -> None:
    with pytest.raises(CycleError):
        topological_order([_step("a", ["b"]), _step("b", ["a"])])


def test_planner_parses_wall_extension() -> None:
    plan = build_plan("give alice 48 hours of wall time on the gpu qos", actor="alice")
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.tool == "slurm.manage_qos"
    assert step.input["name"] == "gpu"
    assert step.input["op"] == "modify"
    assert step.input["max_wall_min"] == 48 * 60


def test_planner_parses_days() -> None:
    plan = build_plan("extend the normal qos wall time to 2 days", actor="bob")
    assert plan.steps[0].input["max_wall_min"] == 2880


def test_planner_unparseable_raises() -> None:
    with pytest.raises(ValueError):
        build_plan("please reticulate the splines", actor="bob")
