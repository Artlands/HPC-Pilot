from __future__ import annotations

import pytest

from hpc_agent.exec import audit
from hpc_agent.exec.runner import (
    CommandRejected,
    CommandSpec,
    redacted_argv,
    run_command,
)


def test_allowlist_rejects_unknown_binary() -> None:
    with pytest.raises(CommandRejected):
        run_command(CommandSpec(argv=["rm", "-rf", "/"]), actor="t", audit_id="a")


def test_allowlist_rejects_empty_argv() -> None:
    with pytest.raises(CommandRejected):
        run_command(CommandSpec(argv=[]), actor="t", audit_id="a")


def test_allowed_binary_runs() -> None:
    res = run_command(CommandSpec(argv=["true"]), actor="t", audit_id="a")
    assert res.rc == 0


def test_redaction_masks_secrets() -> None:
    spec = CommandSpec(argv=["echo", "supersecret"], redact=["supersecret"])
    assert redacted_argv(spec) == ["echo", "***"]


def test_nonzero_rc_does_not_raise() -> None:
    # `git` with a bogus subcommand returns nonzero but must not raise.
    res = run_command(CommandSpec(argv=["git", "--bogus-flag"]), actor="t", audit_id="a")
    assert res.rc != 0


def test_command_audit_is_buffered_until_event_commit() -> None:
    audit.set_sink(audit.InMemorySink())
    event = audit.new_event(actor="alice", tool="test.tool", risk="read", input={})

    res = run_command(CommandSpec(argv=["true"]), actor="alice", audit_id=event.id)
    assert res.rc == 0
    assert audit.get_event(event.id) is None

    audit.commit_event(event)
    committed = audit.get_event(event.id)
    assert committed is not None
    assert [record.argv for record in committed.commands] == [["true"]]
