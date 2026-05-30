from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import hpc_agent.tools.ansible as ansible_mod
from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.safety import gate as safety_gate
from hpc_agent.tools.ansible import RunPlaybookIn, run_playbook
from hpc_agent.tools.result import ToolStatus


class FakePlaybookRunner:
    def __init__(self, *, lint_rc: int = 0, syntax_rc: int = 0, dry_rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.lint_rc = lint_rc
        self.syntax_rc = syntax_rc
        self.dry_rc = dry_rc

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if spec.argv[0] == ansible_mod.ANSIBLE_LINT:
            return CommandResult(rc=self.lint_rc, stdout="", stderr="lint failed", duration_s=0.0)
        if spec.argv[:2] == [ansible_mod.ANSIBLE_PLAYBOOK, "--syntax-check"]:
            return CommandResult(
                rc=self.syntax_rc, stdout="", stderr="syntax failed", duration_s=0.0
            )
        if "--check" in spec.argv:
            return CommandResult(
                rc=self.dry_rc,
                stdout=yaml.safe_dump({"stats": {"cpu01": {"ok": 2, "changed": 1}}}),
                stderr="",
                duration_s=0.0,
            )
        return CommandResult(
            rc=0,
            stdout=yaml.safe_dump({"stats": {"cpu01": {"ok": 3, "changed": 1}}}),
            stderr="",
            duration_s=0.0,
        )


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakePlaybookRunner) -> None:
    monkeypatch.setattr(ansible_mod, "run_command", runner)


def test_run_playbook_refuses_when_lint_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakePlaybookRunner(lint_rc=2)
    _patch_runner(monkeypatch, runner)

    res = run_playbook(RunPlaybookIn(playbook=str(playbook), dry_run=False), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert runner.calls == [[ansible_mod.ANSIBLE_LINT, str(playbook)]]


def test_run_playbook_dry_run_uses_check_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakePlaybookRunner()
    _patch_runner(monkeypatch, runner)

    res = run_playbook(
        RunPlaybookIn(playbook=str(playbook), limit="compute_cpu", dry_run=True),
        actor="alice",
    )

    assert res.status == ToolStatus.DRY_RUN
    playbook_calls = [call for call in runner.calls if call[0] == ansible_mod.ANSIBLE_PLAYBOOK]
    assert playbook_calls[-1] == [
        ansible_mod.ANSIBLE_PLAYBOOK,
        str(playbook),
        "--limit",
        "compute_cpu",
        "--check",
        "--diff",
    ]


def test_run_playbook_apply_runs_after_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakePlaybookRunner()
    _patch_runner(monkeypatch, runner)

    res = run_playbook(
        RunPlaybookIn(playbook=str(playbook), dry_run=False),
        actor="alice",
        gate_override=safety_gate.Gate(requires_approval=True, approved=True, approver="lead"),
    )

    assert res.status == ToolStatus.OK
    assert res.data == {
        "changed_hosts": ["cpu01"],
        "ok": 3,
        "changed": 1,
        "failed": 0,
        "unreachable": 0,
    }
    playbook_calls = [call for call in runner.calls if call[0] == ansible_mod.ANSIBLE_PLAYBOOK]
    assert playbook_calls[-1] == [ansible_mod.ANSIBLE_PLAYBOOK, str(playbook)]


def test_run_playbook_dry_run_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakePlaybookRunner(dry_rc=1)
    _patch_runner(monkeypatch, runner)

    res = run_playbook(RunPlaybookIn(playbook=str(playbook), dry_run=True), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
