"""Tests for warewulf.build_node_image — spec_hash and container exec steps."""

from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.warewulf as ww_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.warewulf import (
    BuildImageIn,
    _build_exec_commands,
    _compute_spec_hash,
    build_node_image,
)

POLICY_DIR = Path(__file__).resolve().parents[2] / "config_repo" / "policy"


class FakeBuildRunner:
    def __init__(self, rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(list(spec.argv))
        return CommandResult(rc=self.rc, stdout="", stderr="", duration_s=0.01)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


@pytest.fixture
def policy() -> PolicyEngine:
    return PolicyEngine.from_dir(POLICY_DIR)


def _patch(monkeypatch: pytest.MonkeyPatch, runner: FakeBuildRunner) -> None:
    monkeypatch.setattr(ww_mod, "run_command", runner)


def _cpu_inp(**overrides: object) -> BuildImageIn:
    defaults: dict = {
        "name": "rockylinux9-cpu",
        "base_image": "base-os",
        "kind": "compute_cpu",
    }
    defaults.update(overrides)
    return BuildImageIn(**defaults)


# --- spec_hash ---


def test_spec_hash_is_deterministic() -> None:
    inp = _cpu_inp()
    assert _compute_spec_hash(inp) == _compute_spec_hash(inp)


def test_spec_hash_differs_by_kind() -> None:
    cpu = _cpu_inp(kind="compute_cpu")
    gpu = BuildImageIn(name="g", base_image="b", kind="compute_gpu")
    assert _compute_spec_hash(cpu) != _compute_spec_hash(gpu)


def test_spec_hash_differs_by_driver_version() -> None:
    a = BuildImageIn(name="g", base_image="b", kind="compute_gpu", nvidia_driver_version="550.0")
    b = BuildImageIn(name="g", base_image="b", kind="compute_gpu", nvidia_driver_version="535.0")
    assert _compute_spec_hash(a) != _compute_spec_hash(b)


def test_spec_hash_ignores_image_name() -> None:
    a = _cpu_inp(name="img-a")
    b = _cpu_inp(name="img-b")
    assert _compute_spec_hash(a) == _compute_spec_hash(b)


def test_spec_hash_packages_order_independent() -> None:
    a = _cpu_inp(packages=["htop", "vim"])
    b = _cpu_inp(packages=["vim", "htop"])
    assert _compute_spec_hash(a) == _compute_spec_hash(b)


def test_spec_hash_is_16_chars() -> None:
    assert len(_compute_spec_hash(_cpu_inp())) == 16


# --- container exec command generation ---


def test_cpu_commands_include_dnf_update() -> None:
    cmds = _build_exec_commands(_cpu_inp())
    update_cmd = [ww_mod.WWCTL, "container", "exec", "rockylinux9-cpu", "--", "dnf", "-y", "update"]
    assert update_cmd in cmds


def test_cpu_commands_install_base_packages() -> None:
    cmds = _build_exec_commands(_cpu_inp())
    install_cmd = next(c for c in cmds if "install" in c and "dnf" in c)
    assert "munge" in install_cmd
    assert "slurm-slurmd" in install_cmd
    assert "chrony" in install_cmd


def test_cpu_commands_end_with_container_build() -> None:
    cmds = _build_exec_commands(_cpu_inp())
    assert cmds[-1] == [ww_mod.WWCTL, "container", "build", "rockylinux9-cpu"]


def test_gpu_commands_include_kernel_devel() -> None:
    inp = BuildImageIn(name="gpu", base_image="b", kind="compute_gpu")
    cmds = _build_exec_commands(inp)
    kd_cmd = next((c for c in cmds if "kernel-devel" in c), None)
    assert kd_cmd is not None


def test_gpu_commands_include_driver_when_set() -> None:
    inp = BuildImageIn(
        name="gpu", base_image="b", kind="compute_gpu", nvidia_driver_version="550.90.07"
    )
    cmds = _build_exec_commands(inp)
    assert any("nvidia-driver-550.90.07" in " ".join(c) for c in cmds)


def test_gpu_commands_include_cuda_when_set() -> None:
    inp = BuildImageIn(
        name="gpu", base_image="b", kind="compute_gpu", cuda_version="12.4"
    )
    cmds = _build_exec_commands(inp)
    assert any("cuda-toolkit-12.4" in " ".join(c) for c in cmds)


def test_gpu_commands_include_fabricmanager_when_enabled() -> None:
    inp = BuildImageIn(
        name="gpu",
        base_image="b",
        kind="compute_gpu",
        nvidia_driver_version="550.90.07",
        enable_fabricmanager=True,
    )
    cmds = _build_exec_commands(inp)
    assert any("nvidia-fabricmanager" in " ".join(c) for c in cmds)


def test_gpu_commands_include_dcgm_by_default() -> None:
    inp = BuildImageIn(name="gpu", base_image="b", kind="compute_gpu", install_dcgm=True)
    cmds = _build_exec_commands(inp)
    assert any("datacenter-gpu-manager" in " ".join(c) for c in cmds)


# --- dry_run (ADMIN role required since RBAC denies OPERATOR) ---


def test_build_image_dry_run_executes_nothing(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBuildRunner()
    _patch(monkeypatch, runner)

    res = build_node_image(
        _cpu_inp(dry_run=True), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.DRY_RUN
    assert runner.calls == []


def test_build_image_dry_run_has_command_preview(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBuildRunner()
    _patch(monkeypatch, runner)

    res = build_node_image(
        _cpu_inp(dry_run=True), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.diff is not None
    assert len(res.diff.commands_preview) > 1  # dnf update + install + container build


# --- live apply (with auto-allow policy) ---


def test_build_image_apply_runs_all_commands(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBuildRunner(rc=0)
    _patch(monkeypatch, runner)

    res = build_node_image(
        _cpu_inp(dry_run=False), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.OK
    expected = _build_exec_commands(_cpu_inp(dry_run=False))
    assert len(runner.calls) == len(expected)


def test_build_image_fails_if_step_fails(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBuildRunner(rc=1)
    _patch(monkeypatch, runner)

    res = build_node_image(
        _cpu_inp(dry_run=False), actor="alice", actor_role=Role.ADMIN, policy=policy
    )

    assert res.status == ToolStatus.ERROR
    assert res.error is not None
    assert res.error.kind.value == "command_failed"
    # Should stop after first failure
    assert len(runner.calls) == 1


def test_build_image_result_contains_spec_hash(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    runner = FakeBuildRunner(rc=0)
    _patch(monkeypatch, runner)

    inp = _cpu_inp(dry_run=False)
    res = build_node_image(inp, actor="alice", actor_role=Role.ADMIN, policy=policy)

    assert res.status == ToolStatus.OK
    assert res.data is not None
    expected_hash = _compute_spec_hash(inp)
    assert res.data["spec_hash"] == expected_hash
    assert len(res.data["spec_hash"]) == 16


def test_build_image_operator_denied(
    monkeypatch: pytest.MonkeyPatch, policy: PolicyEngine
) -> None:
    """OPERATOR role lacks warewulf.build_node_image capability."""
    runner = FakeBuildRunner()
    _patch(monkeypatch, runner)

    res = build_node_image(
        _cpu_inp(dry_run=True), actor="alice", actor_role=Role.OPERATOR, policy=policy
    )

    assert res.status == ToolStatus.DENIED
    assert runner.calls == []
