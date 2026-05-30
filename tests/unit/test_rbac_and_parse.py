from __future__ import annotations

from hpc_agent.exec.rbac import Role, authorize
from hpc_agent.tools.slurm_parse import (
    minutes_to_slurm_time,
    parse_pipe_table,
    slurm_time_to_minutes,
)


def test_viewer_can_query_not_mutate() -> None:
    assert authorize(Role.VIEWER, "slurm.queue")
    assert not authorize(Role.VIEWER, "slurm.manage_qos")


def test_operator_can_manage_slurm() -> None:
    assert authorize(Role.OPERATOR, "slurm.manage_qos")
    assert not authorize(Role.OPERATOR, "warewulf.build_node_image")


def test_admin_can_do_anything() -> None:
    assert authorize(Role.ADMIN, "warewulf.build_node_image")
    assert authorize(Role.ADMIN, "anything.at.all")


def test_time_conversions_roundtrip() -> None:
    assert minutes_to_slurm_time(2880) == "2-00:00:00"
    assert minutes_to_slurm_time(90) == "01:30:00"
    assert slurm_time_to_minutes("2-00:00:00") == 2880
    assert slurm_time_to_minutes("01:30:00") == 90
    assert slurm_time_to_minutes("UNLIMITED") is None


def test_parse_pipe_table() -> None:
    text = "Name|Priority|MaxWall|MaxTRES\ngpu|100|1-00:00:00|gres/gpu=8\n"
    rows = parse_pipe_table(text)
    assert rows == [
        {"Name": "gpu", "Priority": "100", "MaxWall": "1-00:00:00", "MaxTRES": "gres/gpu=8"}
    ]
