from __future__ import annotations

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec.rbac import Role
from hpc_agent.tools.result import ToolResult, ToolStatus
from hpc_agent.tools.slurm import (
    ExtendAccountIn,
    ManageQOSIn,
    ManageUserAssocIn,
    SetLimitsIn,
    set_limits,
)


def test_set_limits_delegates_to_manage_qos(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_manage_qos(
        inp: ManageQOSIn,
        *,
        actor: str,
        actor_role: Role = Role.OPERATOR,
        policy: object | None = None,
        gate_override: object | None = None,
        persist_state: bool = True,
    ) -> ToolResult:
        seen["inp"] = inp
        seen["actor"] = actor
        seen["role"] = actor_role
        seen["persist_state"] = persist_state
        return ToolResult.success(data={"delegated": "qos"})

    monkeypatch.setattr(slurm_mod, "manage_qos", fake_manage_qos)

    res = set_limits(
        SetLimitsIn(target="qos", name="gpu", max_wall_min=2880, max_tres="gres/gpu=8"),
        actor="alice",
        actor_role=Role.OPERATOR,
        persist_state=False,
    )

    assert res.status == ToolStatus.OK
    assert seen["actor"] == "alice"
    assert seen["role"] == Role.OPERATOR
    assert seen["persist_state"] is False
    inp = seen["inp"]
    assert isinstance(inp, ManageQOSIn)
    assert inp.name == "gpu"
    assert inp.op == "modify"
    assert inp.max_wall_min == 2880
    assert inp.max_tres == "gres/gpu=8"


def test_set_limits_delegates_to_extend_account(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_extend_account(
        inp: ExtendAccountIn,
        *,
        actor: str,
        actor_role: Role = Role.OPERATOR,
        policy: object | None = None,
        gate_override: object | None = None,
        persist_state: bool = True,
    ) -> ToolResult:
        seen["inp"] = inp
        return ToolResult.success(data={"delegated": "account"})

    monkeypatch.setattr(slurm_mod, "extend_account", fake_extend_account)

    res = set_limits(
        SetLimitsIn(target="account", name="research", grp_tres="cpu=512"),
        actor="alice",
    )

    assert res.status == ToolStatus.OK
    inp = seen["inp"]
    assert isinstance(inp, ExtendAccountIn)
    assert inp.name == "research"
    assert inp.op == "modify"
    assert inp.grp_tres == "cpu=512"


def test_set_limits_delegates_to_manage_user_assoc(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def fake_manage_user_assoc(
        inp: ManageUserAssocIn,
        *,
        actor: str,
        actor_role: Role = Role.OPERATOR,
        policy: object | None = None,
        gate_override: object | None = None,
        persist_state: bool = True,
    ) -> ToolResult:
        seen["inp"] = inp
        return ToolResult.success(data={"delegated": "assoc"})

    monkeypatch.setattr(slurm_mod, "manage_user_assoc", fake_manage_user_assoc)

    res = set_limits(
        SetLimitsIn(
            target="user_assoc",
            user="alice",
            account="research",
            qos_add=["gpu"],
            default_qos="gpu",
        ),
        actor="alice",
    )

    assert res.status == ToolStatus.OK
    inp = seen["inp"]
    assert isinstance(inp, ManageUserAssocIn)
    assert inp.user == "alice"
    assert inp.account == "research"
    assert inp.qos_add == ["gpu"]
    assert inp.default_qos == "gpu"


def test_set_limits_missing_target_identity() -> None:
    res = set_limits(SetLimitsIn(target="qos", max_wall_min=60), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"
