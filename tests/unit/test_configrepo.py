from __future__ import annotations

from pathlib import Path

from hpc_agent.exec import audit
from hpc_agent.state.configrepo import ConfigRepo


def test_configrepo_uses_runner_audited_git_commands(tmp_path: Path) -> None:
    sink = audit.InMemorySink()
    audit.set_sink(sink)

    repo = ConfigRepo(str(tmp_path))
    repo.stage("policy/example.yaml", "[]\n")
    diff = repo.diff()
    commit = repo.commit("add example policy")

    assert "policy/example.yaml" in diff
    assert commit
    events = [event for event in sink.events.values() if event.tool == "configrepo.git"]
    assert events
    assert any(record.argv[:2] == ["git", "add"] for event in events for record in event.commands)
    assert any("commit" in record.argv for event in events for record in event.commands)
