from __future__ import annotations

import pytest

import hpc_agent.tools.slurm as slurm_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandResult, CommandSpec
from hpc_agent.tools.result import ToolStatus
from hpc_agent.tools.slurm import UsageReportIn, usage_report

USAGE_ROWS = (
    "Cluster|Login|Proper Name|Account|Used|Energy\n"
    "cluster|alice|Alice User|research|3600|0\n"
)


class FakeUsageRunner:
    def __init__(self, *, rc: int = 0, stdout: str = USAGE_ROWS) -> None:
        self.calls: list[list[str]] = []
        self.rc = rc
        self.stdout = stdout

    def __call__(self, spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
        self.calls.append(spec.argv)
        return CommandResult(rc=self.rc, stdout=self.stdout, stderr="report failed", duration_s=0.0)


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


def _patch_runner(monkeypatch: pytest.MonkeyPatch, runner: FakeUsageRunner) -> None:
    monkeypatch.setattr(slurm_mod, "run_command", runner)


def test_usage_report_filters_as_viewer(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeUsageRunner()
    _patch_runner(monkeypatch, runner)

    res = usage_report(
        UsageReportIn(
            start="2026-05-01",
            end="2026-05-02",
            account="research",
            user="alice",
        ),
        actor="alice",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.OK
    assert res.data == {
        "usage": [
            {
                "cluster": "cluster",
                "login": "alice",
                "proper_name": "Alice User",
                "account": "research",
                "used": "3600",
                "energy": "0",
                "Cluster": "cluster",
                "Login": "alice",
                "Proper Name": "Alice User",
                "Account": "research",
                "Used": "3600",
                "Energy": "0",
            }
        ]
    }
    assert runner.calls == [
        [
            slurm_mod.SREPORT,
            "cluster",
            "user",
            "Utilization",
            "-P",
            "Start=2026-05-01",
            "End=2026-05-02",
            "Accounts=research",
            "Users=alice",
        ]
    ]


def test_usage_report_all(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeUsageRunner(stdout="Cluster|Login|Used\n")
    _patch_runner(monkeypatch, runner)

    res = usage_report(UsageReportIn(), actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data == {"usage": []}
    assert runner.calls == [[slurm_mod.SREPORT, "cluster", "user", "Utilization", "-P"]]


def test_usage_report_command_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = FakeUsageRunner(rc=1)
    _patch_runner(monkeypatch, runner)

    res = usage_report(UsageReportIn(user="alice"), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "command_failed"
