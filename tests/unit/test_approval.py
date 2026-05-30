from __future__ import annotations

import pytest

from hpc_agent.safety.approval import request_approval
from hpc_agent.safety.diff import Diff
from hpc_agent.safety.gate import Gate


def test_api_approval_backend_returns_pending_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APPROVAL_BACKEND", "api")

    gate = request_approval(
        Gate(requires_approval=True, reason="medium-risk action"),
        Diff(),
        "alice",
    )

    assert gate.requires_approval is True
    assert gate.approved is False
    assert gate.denied is False
    assert gate.reason == "medium-risk action"
