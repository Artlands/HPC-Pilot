from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from hpc_agent.safety.policy import Effect, PolicyEngine

POLICY_DIR = Path(__file__).resolve().parents[2] / "config_repo" / "policy"


@pytest.fixture
def engine() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def test_inpolicy_modify_autoallows(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        tool="slurm.manage_qos",
        domain="slurm",
        risk="medium",
        op="modify",
        input_ctx={"name": "gpu", "max_wall_min": 2880, "max_tres": "gres/gpu=8"},
    )
    assert d.effect == Effect.AUTO


def test_over_wall_cap_denies(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        tool="slurm.manage_qos",
        domain="slurm",
        risk="medium",
        op="modify",
        input_ctx={"name": "gpu", "max_wall_min": 100000},
    )
    assert d.effect == Effect.DENY


def test_over_gpu_cap_denies(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        tool="slurm.manage_qos",
        domain="slurm",
        risk="medium",
        op="modify",
        input_ctx={"name": "gpu", "max_tres": "cpu=10,gres/gpu=128"},
    )
    assert d.effect == Effect.DENY


def test_create_requires_approval(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        tool="slurm.manage_qos",
        domain="slurm",
        risk="medium",
        op="create",
        input_ctx={"name": "newq"},
    )
    assert d.effect == Effect.REQUIRE_APPROVAL


def test_node_blast_cap_requires_approval(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        tool="warewulf.assign_image_to_nodes",
        domain="warewulf",
        risk="medium",
        op="modify",
        input_ctx={"blast_radius": 20},
    )
    assert d.effect == Effect.REQUIRE_APPROVAL


def test_blackout_window_denies_medium_risk_inside_window(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        tool="slurm.manage_qos",
        domain="slurm",
        risk="medium",
        op="modify",
        input_ctx={"name": "gpu", "max_wall_min": 60},
        now=datetime(2026, 6, 1, 2, 30, tzinfo=ZoneInfo("America/Chicago")),
    )
    assert d.effect == Effect.DENY
    assert d.rule_id == "blackout-window"


def test_blackout_window_allows_fallthrough_outside_window(engine: PolicyEngine) -> None:
    d = engine.evaluate(
        tool="slurm.manage_qos",
        domain="slurm",
        risk="medium",
        op="modify",
        input_ctx={"name": "gpu", "max_wall_min": 60},
        now=datetime(2026, 6, 1, 5, 0, tzinfo=ZoneInfo("America/Chicago")),
    )
    assert d.effect == Effect.AUTO
