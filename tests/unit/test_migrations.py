from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_creates_state_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "state.sqlite"
    monkeypatch.setenv("HPC_DB_URL", f"sqlite+pysqlite:///{db_path}")

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{db_path}")
    command.upgrade(cfg, "head")

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "accounts",
        "alembic_version",
        "images",
        "nodes",
        "partition_members",
        "partitions",
        "qos",
        "user_assocs",
    }

    node_columns = {col["name"] for col in inspector.get_columns("nodes")}
    assert {
        "hostname",
        "role",
        "state",
        "image_id",
        "gpu_count",
        "cpu_count",
        "mem_mb",
        "updated_at",
    }.issubset(node_columns)

    assoc_unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("user_assocs")
    }
    assert ("user", "account") in assoc_unique_constraints
