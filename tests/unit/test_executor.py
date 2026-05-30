from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.core.executor import resume_plan, run_plan
from hpc_agent.core.plan import PlanState, Step, StepStatus
from hpc_agent.core.planner import plan_from_steps
from hpc_agent.core.planstore import InMemoryPlanStore, set_store
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety.policy import PolicyEngine

POLICY_DIR = Path(__file__).resolve().parents[2] / "config_repo" / "policy"

GPU_ROW = (
    "Name|Priority|MaxWall|MaxJobsPU|MaxTRES|MaxTRESPU|GrpTRES\n"
    "gpu|100|1-00:00:00||gres/gpu=8||\n"
)


class FakeRunner:
    def __init__(self, row: str | None) -> None:
        self.calls: list[list[str]] = []
        self.row = row

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if "show" in spec.argv:
            return CommandResult(rc=0, stdout=self.row or "", stderr="", duration_s=0.0)
        return CommandResult(rc=0, stdout="", stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh() -> None:
    audit.set_sink(audit.InMemorySink())
    set_store(InMemoryPlanStore())


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def _patch(monkeypatch: pytest.MonkeyPatch, runner: FakeRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)
    # tool persists state best-effort; disable DB writes in these tests
    monkeypatch.setattr(slurm_mod, "_persist_qos", lambda _inp: None)


def test_inpolicy_plan_runs_to_completion(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    _patch(monkeypatch, FakeRunner(GPU_ROW))
    step = Step(
        id="s1",
        tool="slurm.manage_qos",
        input={"name": "gpu", "op": "modify", "max_wall_min": 2880},
    )
    plan = plan_from_steps("extend gpu wall", "alice", [step])
    out = run_plan(plan, policy=policy)
    assert out.state == PlanState.DONE
    assert out.step("s1").status == StepStatus.DONE


def test_create_pauses_then_resumes(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeRunner(row=None)  # qos doesn't exist yet -> create
    _patch(monkeypatch, runner)
    step = Step(
        id="s1",
        tool="slurm.manage_qos",
        input={"name": "newq", "op": "create", "priority": 50},
    )
    plan = plan_from_steps("create newq", "alice", [step])

    paused = run_plan(plan, policy=policy)
    assert paused.state == PlanState.PAUSED
    assert paused.step("s1").status == StepStatus.NEEDS_APPROVAL
    assert all("add" not in c for c in runner.calls)  # not yet executed

    resumed = resume_plan(plan.id, "s1", approver="admin@site", policy=policy)
    assert resumed.state == PlanState.DONE
    assert resumed.step("s1").status == StepStatus.DONE
    assert any("add" in c for c in runner.calls)  # executed after approval


def test_outofpolicy_step_fails_plan(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    _patch(monkeypatch, FakeRunner(GPU_ROW))
    step = Step(
        id="s1",
        tool="slurm.manage_qos",
        input={"name": "gpu", "op": "modify", "max_wall_min": 100000},  # > 3d cap
    )
    plan = plan_from_steps("over cap", "alice", [step])
    out = run_plan(plan, policy=policy)
    assert out.state == PlanState.FAILED
    assert out.step("s1").status == StepStatus.FAILED


def test_dependent_step_skipped_after_failure(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    _patch(monkeypatch, FakeRunner(GPU_ROW))
    bad = Step(
        id="bad",
        tool="slurm.manage_qos",
        input={"name": "gpu", "op": "modify", "max_wall_min": 100000},
        critical=False,  # don't halt; let the dependent get skipped
    )
    dependent = Step(
        id="dep",
        tool="slurm.manage_qos",
        input={"name": "gpu", "op": "modify", "max_wall_min": 1440},
        depends_on=["bad"],
    )
    plan = plan_from_steps("two steps", "alice", [bad, dependent])
    out = run_plan(plan, policy=policy)
    assert out.step("bad").status == StepStatus.FAILED
    assert out.step("dep").status == StepStatus.SKIPPED


def test_resume_rejects_changed_diff(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeRunner(row=None)
    _patch(monkeypatch, runner)
    step = Step(
        id="s1",
        tool="slurm.manage_qos",
        input={"name": "newq", "op": "create", "priority": 50},
    )
    plan = plan_from_steps("create newq", "alice", [step])
    run_plan(plan, policy=policy)  # pauses

    # tamper: the persisted step's recorded diff hash no longer matches reality
    plan.step("s1").diff_hash = "stale-hash-that-wont-match"
    out = resume_plan(plan.id, "s1", approver="admin@site", policy=policy)
    assert out.state == PlanState.FAILED
    assert out.step("s1").status == StepStatus.FAILED
    assert out.step("s1").result is not None
    assert "invalidated" in (out.step("s1").result.error.message)  # type: ignore[union-attr]
