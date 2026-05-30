from __future__ import annotations

from pathlib import Path

from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandSpec, run_command


def test_database_audit_sink_persists_events_and_commands(tmp_path: Path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'audit.sqlite'}"
    sink = audit.use_database_sink(db_url, init_schema=True)

    event = audit.new_event(
        actor="alice",
        tool="slurm.manage_qos",
        risk="medium",
        input={"name": "gpu"},
    )
    run_command(CommandSpec(argv=["true"]), actor="alice", audit_id=event.id)
    event.decision = "auto"
    event.result_status = "ok"
    event.diff_summary = "qos/gpu"
    audit.commit_event(event)

    loaded = audit.get_event(event.id)
    assert loaded is not None
    assert loaded.id == event.id
    assert loaded.actor == "alice"
    assert loaded.tool == "slurm.manage_qos"
    assert [command.argv for command in loaded.commands] == [["true"]]

    listed = sink.list_events(result_status="ok")
    assert [item.id for item in listed] == [event.id]


def test_database_audit_sink_records_commands_after_event_commit(tmp_path: Path) -> None:
    db_url = f"sqlite+pysqlite:///{tmp_path / 'audit.sqlite'}"
    audit.use_database_sink(db_url, init_schema=True)

    event = audit.new_event(actor="alice", tool="test.tool", risk="read", input={})
    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    run_command(CommandSpec(argv=["true"]), actor="alice", audit_id=event.id)

    loaded = audit.get_event(event.id)
    assert loaded is not None
    assert [command.argv for command in loaded.commands] == [["true"]]
