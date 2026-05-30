from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.core.plan import PlanState
from hpc_agent.core.planstore import InMemoryPlanStore, set_store
from hpc_agent.core.shell import ShellSession
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety.policy import PolicyEngine

POLICY_DIR = Path(__file__).resolve().parents[2] / "config_repo" / "policy"

GPU_ROW = (
    "Name|Priority|MaxWall|MaxJobsPU|MaxTRES|MaxTRESPU|GrpTRES\n"
    "gpu|100|1-00:00:00||gres/gpu=8||\n"
)


class FakeRunner:
    def __init__(self, row: str | None = GPU_ROW) -> None:
        self.calls: list[list[str]] = []
        self.row = row

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if "show" in spec.argv:
            return CommandResult(rc=0, stdout=self.row or "", stderr="", duration_s=0.0)
        return CommandResult(rc=0, stdout="", stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    audit.set_sink(audit.InMemorySink())
    set_store(InMemoryPlanStore())
    monkeypatch.setattr(slurm_mod, "_persist_qos", lambda _inp: None)


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def _session(policy: PolicyEngine) -> tuple[ShellSession, list[str]]:
    writes: list[str] = []
    session = ShellSession(
        actor="alice",
        actor_role=Role.OPERATOR,
        policy=policy,
        write=writes.append,
    )
    return session, writes


def test_shell_builds_plan_from_plain_intent(policy: PolicyEngine) -> None:
    session, writes = _session(policy)

    session.handle_line("give alice 48 hours of wall time on the gpu qos")

    assert session.current_plan is not None
    assert session.current_plan.steps[0].tool == "slurm.manage_qos"
    assert "Use /run to execute" in writes[-1]


def test_shell_run_executes_current_plan(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeRunner()
    monkeypatch.setattr(slurm_mod, "run_command", runner)
    session, writes = _session(policy)

    session.handle_line("give alice 48 hours of wall time on the gpu qos")
    session.handle_line("/run")

    assert session.current_plan is not None
    assert session.current_plan.state == PlanState.DONE
    assert any("modify" in call for call in runner.calls)
    assert any("result=ok" in output for output in writes)


def test_shell_reports_unparseable_intent(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeRunner(row=None)
    monkeypatch.setattr(slurm_mod, "run_command", runner)
    session, writes = _session(policy)

    session.handle_line("/run create gpu qos 1 hour")

    assert session.current_plan is None
    assert any("Could not build a plan" in output for output in writes)


def test_shell_approve_resumes_paused_create(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeRunner(row=None)
    monkeypatch.setattr(slurm_mod, "run_command", runner)
    session, writes = _session(policy)

    from hpc_agent.core.plan import Step
    from hpc_agent.core.planner import plan_from_steps

    session.current_plan = plan_from_steps(
        "create newq",
        "alice",
        [
            Step(
                id="s1",
                tool="slurm.manage_qos",
                input={"name": "newq", "op": "create", "priority": 1},
            )
        ],
    )
    session.handle_line("/run")
    assert session.current_plan.state == PlanState.PAUSED
    assert any("Plan paused for approval" in output for output in writes)

    session.handle_line("/approve")

    assert session.current_plan is not None
    assert session.current_plan.state.value == PlanState.DONE.value
    assert any("add" in call for call in runner.calls)


def test_shell_lists_tools(policy: PolicyEngine) -> None:
    session, writes = _session(policy)

    session.handle_line("/tools")

    assert writes[0] == "Registered tools:"
    assert any("slurm.manage_qos" in output for output in writes)
