from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import ManageUserAssocIn, manage_user_assoc

POLICY_DIR = Path(__file__).resolve().parents[2] / "config_repo" / "policy"

QOS_GPU_ROW = (
    "Name|Priority|MaxWall|MaxJobsPU|MaxTRES|MaxTRESPU|GrpTRES\ngpu|100|1-00:00:00||gres/gpu=8||\n"
)
QOS_NORMAL_ROW = (
    "Name|Priority|MaxWall|MaxJobsPU|MaxTRES|MaxTRESPU|GrpTRES\nnormal|1|1-00:00:00||||\n"
)
ASSOC_ROW = "User|Account|QOS|DefaultQOS|FairShare\nalice|research|normal|normal|100\n"


class FakeAssocRunner:
    def __init__(
        self,
        *,
        assoc_row: str | None = ASSOC_ROW,
        qos_rows: dict[str, str] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.assoc_row = assoc_row
        self.qos_rows = (
            qos_rows if qos_rows is not None else {"gpu": QOS_GPU_ROW, "normal": QOS_NORMAL_ROW}
        )

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if spec.argv[:3] == [slurm_mod.SACCTMGR, "show", "qos"]:
            name = spec.argv[3]
            return CommandResult(
                rc=0,
                stdout=self.qos_rows.get(name, ""),
                stderr="",
                duration_s=0.0,
            )
        if spec.argv[:3] == [slurm_mod.SACCTMGR, "show", "assoc"]:
            return CommandResult(
                rc=0,
                stdout=self.assoc_row or "",
                stderr="",
                duration_s=0.0,
            )
        return CommandResult(rc=0, stdout="", stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeAssocRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_assoc_dry_run_mutates_nothing(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeAssocRunner()
    _patch_runner(monkeypatch, runner)

    inp = ManageUserAssocIn(
        user="alice",
        account="research",
        op="modify",
        qos_add=["gpu"],
        dry_run=True,
    )
    res = manage_user_assoc(inp, actor="alice", policy=policy)

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert all("modify" not in call and "add" not in call for call in runner.calls)


def test_assoc_missing_qos_precondition(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAssocRunner(qos_rows={"normal": QOS_NORMAL_ROW})
    _patch_runner(monkeypatch, runner)

    inp = ManageUserAssocIn(
        user="alice",
        account="research",
        op="modify",
        qos_add=["missing"],
        dry_run=False,
    )
    res = manage_user_assoc(inp, actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"
    assert all("modify" not in call for call in runner.calls)


def test_assoc_apply_records_inverse(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAssocRunner()
    _patch_runner(monkeypatch, runner)

    inp = ManageUserAssocIn(
        user="alice",
        account="research",
        op="modify",
        qos_add=["gpu"],
        default_qos="gpu",
        dry_run=False,
    )
    res = manage_user_assoc(
        inp,
        actor="alice",
        gate_override=safety_gate.Gate(requires_approval=True, approved=True, approver="lead"),
    )

    assert res.status == ToolStatus.OK
    modify_calls = [call for call in runner.calls if "modify" in call]
    assert modify_calls
    assert any("QOS+=gpu" in tok for tok in modify_calls[0])
    assert any("DefaultQOS=gpu" in tok for tok in modify_calls[0])

    event = audit.get_event(res.audit_id or "")
    assert event is not None and event.revert_argv
    inverse_tokens = [tok for argv in event.revert_argv for tok in argv]
    assert "QOS=normal" in inverse_tokens
    assert "DefaultQOS=normal" in inverse_tokens


def test_assoc_create_requires_approval(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeAssocRunner(assoc_row=None)
    _patch_runner(monkeypatch, runner)

    inp = ManageUserAssocIn(
        user="bob",
        account="research",
        op="create",
        qos_list=["normal"],
        default_qos="normal",
        dry_run=False,
    )
    res = manage_user_assoc(inp, actor="alice", policy=policy)

    assert res.status == ToolStatus.NEEDS_APPROVAL
    assert all("add" not in call and "modify" not in call for call in runner.calls)


def test_assoc_idempotent_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeAssocRunner()
    _patch_runner(monkeypatch, runner)

    inp = ManageUserAssocIn(
        user="alice",
        account="research",
        op="modify",
        qos_add=["normal"],
        default_qos="normal",
        fairshare=100,
        dry_run=False,
    )
    res = manage_user_assoc(inp, actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data and res.data.get("noop") is True
    assert all("modify" not in call for call in runner.calls)
