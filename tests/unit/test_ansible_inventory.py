from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import hpc_agent.tools.ansible as ansible_mod
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.state.db import configure, init_db, session_scope
from hpc_agent.state.models import NodeRole, NodeState
from hpc_agent.state.repos import NodeRepo
from hpc_agent.tools.ansible import ManageInventoryIn, manage_inventory
from hpc_agent.tools.result import ToolStatus


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audit.set_sink(audit.InMemorySink())
    configure("sqlite+pysqlite:///:memory:")
    init_db()
    monkeypatch.setattr(ansible_mod.settings, "ansible_dir", str(tmp_path))


def _seed_nodes() -> None:
    with session_scope() as session:
        repo = NodeRepo(session)
        repo.upsert(
            "cpu01",
            role=NodeRole.COMPUTE_CPU,
            state=NodeState.UP,
            ip="10.0.0.11",
            cpu_count=64,
            mem_mb=256000,
            features="zen4",
        )
        repo.upsert(
            "gpu01",
            role=NodeRole.COMPUTE_GPU,
            state=NodeState.UP,
            ip="10.0.0.21",
            gpu_count=8,
            gpu_model="a100",
        )


def test_manage_inventory_dry_run_writes_nothing(tmp_path: Path) -> None:
    _seed_nodes()

    res = manage_inventory(ManageInventoryIn(dry_run=True), actor="alice")

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert not (tmp_path / "inventory" / "hosts.yml").exists()


def test_manage_inventory_apply_writes_state_groups(tmp_path: Path) -> None:
    _seed_nodes()

    res = manage_inventory(ManageInventoryIn(dry_run=False), actor="alice")

    assert res.status == ToolStatus.OK
    path = tmp_path / "inventory" / "hosts.yml"
    assert path.exists()
    inventory = yaml.safe_load(path.read_text())
    assert inventory == {
        "all": {
            "children": {
                "compute_cpu": {
                    "hosts": {
                        "cpu01": {
                            "ansible_host": "10.0.0.11",
                            "cpu_count": 64,
                            "features": "zen4",
                            "mem_mb": 256000,
                        }
                    }
                },
                "compute_gpu": {
                    "hosts": {
                        "gpu01": {
                            "ansible_host": "10.0.0.21",
                            "gpu_count": 8,
                            "gpu_model": "a100",
                        }
                    }
                },
            }
        }
    }


def test_manage_inventory_idempotent_noop(tmp_path: Path) -> None:
    _seed_nodes()
    first = manage_inventory(ManageInventoryIn(dry_run=False), actor="alice")
    assert first.status == ToolStatus.OK

    second = manage_inventory(ManageInventoryIn(dry_run=False), actor="alice")

    assert second.status == ToolStatus.OK
    assert second.data and second.data.get("noop") is True


def test_manage_inventory_viewer_denied() -> None:
    _seed_nodes()

    res = manage_inventory(
        ManageInventoryIn(dry_run=False),
        actor="alice",
        actor_role=Role.VIEWER,
    )

    assert res.status == ToolStatus.DENIED
