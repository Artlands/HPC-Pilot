"""Shared pytest fixtures for HPC Pilot tests."""
from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator
from typing import Any

import pytest


@pytest.fixture()
def tmp_home(tmp_path: Any) -> Generator[str, None, None]:
    """Set HPC_PILOT_HOME to a temp directory and create the standard layout.

    Yields the home path string.  Restores the original env var on teardown.
    """
    home = str(tmp_path / ".hpc-pilot")
    original = os.environ.get("HPC_PILOT_HOME")
    os.environ["HPC_PILOT_HOME"] = home

    from hpc_pilot.paths import ensure_layout
    ensure_layout()

    yield home

    if original is None:
        os.environ.pop("HPC_PILOT_HOME", None)
    else:
        os.environ["HPC_PILOT_HOME"] = original


@pytest.fixture()
def mock_cluster(tmp_home: str) -> Any:
    """Return a Cluster instance with deterministic paths inside tmp_home."""
    from hpc_pilot.clusters import Cluster

    return Cluster(
        name="default",
        slurm_bin_dir="/usr/bin",
        warewulf_bin_dir="/usr/bin",
        spack_root="/opt/spack",
        ansible_dir=os.path.join(tmp_home, "ansible"),
        ssh=None,
    )


@pytest.fixture()
def audit_records(tmp_home: str) -> Callable[[], list[dict[str, Any]]]:
    """Return a callable that reads and parses all audit.jsonl records."""
    audit_path = os.path.join(tmp_home, "logs", "audit.jsonl")

    def _read() -> list[dict[str, Any]]:
        if not os.path.exists(audit_path):
            return []
        records = []
        with open(audit_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    return _read
