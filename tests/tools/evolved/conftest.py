"""Shared fixtures for auto-generated tool tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def mock_cluster():
    cl = MagicMock()
    cl.warewulf.side_effect = lambda name: f"/usr/bin/{name}"
    cl.slurm.side_effect = lambda name: f"/usr/bin/{name}"
    cl.spack = "/opt/spack"
    cl.ssh = None
    return cl


@pytest.fixture()
def mock_run(request):
    """Auto-patch _run in the module under test for the duration of the test.

    Usage:
        class TestFoo:
            @pytest.mark.usefixtures("mock_run")
            def test_thing(self):
                hpc_foo_bar(...)
                hpc_foo_bar._run.assert_called_once_with(...)

    The evolved tool's module is determined from ``request.module.__name__``,
    so the fixture must be used in a test file inside ``tests/tools/evolved/``
    with the standard naming pattern.
    """
    test_mod = request.module.__name__
    patch_path = f"{test_mod}._run"
    with patch(patch_path, return_value="mock output") as m:
        yield m
