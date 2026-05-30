from __future__ import annotations

from pathlib import Path

import pytest

import hpc_agent.tools.ansible as ansible_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.tools.ansible import ManageSecretIn, manage_secret
from hpc_agent.tools.result import ToolStatus


@pytest.fixture(autouse=True)
def fresh_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit.set_sink(audit.InMemorySink())
    monkeypatch.setattr(ansible_mod.settings, "ansible_dir", str(tmp_path))


def test_manage_secret_present_does_not_leak_material(tmp_path: Path) -> None:
    secret = tmp_path / "secrets" / "munge" / "key"
    secret.parent.mkdir(parents=True)
    secret.write_text("super-secret-material")

    res = manage_secret(ManageSecretIn(ref="munge/key"), actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data == {"ref": "munge/key", "present": True}
    assert "super-secret-material" not in res.model_dump_json()

    event = audit.get_event(res.audit_id or "")
    assert event is not None
    assert "super-secret-material" not in event.model_dump_json()


def test_manage_secret_missing_returns_not_found() -> None:
    res = manage_secret(ManageSecretIn(ref="munge/key"), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "not_found"


def test_manage_secret_rejects_path_traversal() -> None:
    res = manage_secret(ManageSecretIn(ref="../outside"), actor="alice")

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"


def test_manage_secret_viewer_denied(tmp_path: Path) -> None:
    secret = tmp_path / "secrets" / "munge" / "key"
    secret.parent.mkdir(parents=True)
    secret.write_text("super-secret-material")

    res = manage_secret(
        ManageSecretIn(ref="munge/key"),
        actor="alice",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.DENIED
    assert "super-secret-material" not in res.model_dump_json()
