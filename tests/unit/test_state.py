from __future__ import annotations

import pytest

from hpc_agent.state.db import configure, init_db, session_scope
from hpc_agent.state.models import NodeRole, NodeState
from hpc_agent.state.repos import ImageRepo, NodeRepo, SlurmRepo


@pytest.fixture(autouse=True)
def memory_db() -> None:
    configure("sqlite+pysqlite:///:memory:")
    init_db()


def test_qos_upsert_inserts_then_updates() -> None:
    with session_scope() as s:
        repo = SlurmRepo(s)
        repo.upsert_qos("gpu", priority=100, max_wall_min=1440)
    with session_scope() as s:
        repo = SlurmRepo(s)
        q = repo.get_qos("gpu")
        assert q is not None and q.priority == 100 and q.max_wall_min == 1440
    # update only one field; others preserved (upsert ignores None)
    with session_scope() as s:
        SlurmRepo(s).upsert_qos("gpu", max_wall_min=2880)
    with session_scope() as s:
        q = SlurmRepo(s).get_qos("gpu")
        assert q is not None and q.max_wall_min == 2880 and q.priority == 100


def test_node_repo_queries_by_role_and_state() -> None:
    with session_scope() as s:
        repo = NodeRepo(s)
        repo.upsert("cpu01", role=NodeRole.COMPUTE_CPU, state=NodeState.UP, cpu_count=64)
        repo.upsert("gpu01", role=NodeRole.COMPUTE_GPU, state=NodeState.DOWN, gpu_count=8)
    with session_scope() as s:
        repo = NodeRepo(s)
        assert {n.hostname for n in repo.by_role(NodeRole.COMPUTE_GPU)} == {"gpu01"}
        assert {n.hostname for n in repo.by_state(NodeState.DOWN)} == {"gpu01"}
        assert len(repo.all()) == 2


def test_image_repo_by_spec_hash() -> None:
    with session_scope() as s:
        ImageRepo(s).upsert(
            "gpu-rocky9", base_os="rockylinux:9", kind=NodeRole.COMPUTE_GPU, spec_hash="abc123"
        )
    with session_scope() as s:
        img = ImageRepo(s).by_spec_hash("abc123")
        assert img is not None and img.name == "gpu-rocky9"


def test_user_assoc_unique_per_user_account() -> None:
    with session_scope() as s:
        repo = SlurmRepo(s)
        repo.upsert_assoc("alice", "physics", qos_list="normal,gpu", default_qos="normal")
        repo.upsert_assoc("alice", "physics", default_qos="gpu")  # update same row
    with session_scope() as s:
        a = SlurmRepo(s).get_assoc("alice", "physics")
        assert a is not None and a.default_qos == "gpu" and a.qos_list == "normal,gpu"
