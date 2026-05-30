from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.ansible as ansible_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.ansible import LintPlaybookIn, lint_playbook
from hpc_agent.tools.result import ToolStatus


class FakeAnsibleRunner:
    def __init__(self, *, lint_rc: int = 0, syntax_rc: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.lint_rc = lint_rc
        self.syntax_rc = syntax_rc

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        if spec.argv[0] == ansible_mod.ANSIBLE_LINT:
            return CommandResult(
                rc=self.lint_rc, stdout="lint output", stderr="lint error", duration_s=0.0
            )
        return CommandResult(
            rc=self.syntax_rc, stdout="syntax output", stderr="syntax error", duration_s=0.0
        )


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeAnsibleRunner) -> None:
    monkeypatch.setattr(ansible_mod, "run_command", runner)


def test_lint_playbook_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakeAnsibleRunner()
    _patch_runner(monkeypatch, runner)

    res = lint_playbook(LintPlaybookIn(playbook=str(playbook)), actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data == {"playbook": str(playbook), "lint": "passed", "syntax": "passed"}
    assert runner.calls == [
        [ansible_mod.ANSIBLE_LINT, str(playbook)],
        [ansible_mod.ANSIBLE_PLAYBOOK, "--syntax-check", str(playbook)],
    ]


def test_lint_playbook_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runner = FakeAnsibleRunner()
    _patch_runner(monkeypatch, runner)

    res = lint_playbook(LintPlaybookIn(playbook=str(tmp_path / "missing.yml")), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"
    assert runner.calls == []


def test_lint_playbook_lint_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakeAnsibleRunner(lint_rc=2)
    _patch_runner(monkeypatch, runner)

    res = lint_playbook(LintPlaybookIn(playbook=str(playbook)), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
    assert runner.calls == [[ansible_mod.ANSIBLE_LINT, str(playbook)]]


def test_lint_playbook_syntax_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakeAnsibleRunner(syntax_rc=4)
    _patch_runner(monkeypatch, runner)

    res = lint_playbook(LintPlaybookIn(playbook=str(playbook)), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
    assert runner.calls == [
        [ansible_mod.ANSIBLE_LINT, str(playbook)],
        [ansible_mod.ANSIBLE_PLAYBOOK, "--syntax-check", str(playbook)],
    ]


def test_lint_playbook_viewer_denied(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    playbook = tmp_path / "site.yml"
    playbook.write_text("- hosts: all\n  roles: []\n")
    runner = FakeAnsibleRunner()
    _patch_runner(monkeypatch, runner)

    res = lint_playbook(
        LintPlaybookIn(playbook=str(playbook)),
        actor="alice",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.DENIED
    assert runner.calls == []
