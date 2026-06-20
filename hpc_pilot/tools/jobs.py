"""Job-management tools exposed to the agent."""

from __future__ import annotations

from typing import Any

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool


@hpc_tool(
    name="hpc_job_status",
    role=Role.VIEWER,
    schema={
        "name": "hpc_job_status",
        "description": "Check the status of a background job (Spack install, Ansible run).",
        "input_schema": {
            "type": "object",
            "properties": {"run_id": {"type": "string", "description": "Job run ID"}},
            "required": ["run_id"],
        },
    },
)
def hpc_job_status(run_id: str) -> dict[str, Any]:
    """Return the status of a background job (Spack install, Ansible run)."""
    from hpc_pilot.jobs import get_job

    record = get_job(run_id)
    if record is None:
        return {"run_id": run_id, "status": "not_found"}
    return {
        "run_id": record.run_id,
        "status": record.status,
        "returncode": record.returncode,
        "error": record.error,
        "started_at": record.started_at,
        "cmd": record.cmd,
    }


@hpc_tool(
    name="hpc_job_logs",
    role=Role.VIEWER,
    schema={
        "name": "hpc_job_logs",
        "description": "View the last N lines of a background job's log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "tail": {
                    "type": "integer",
                    "description": "Number of lines to show (default: 200)",
                },
            },
            "required": ["run_id"],
        },
    },
)
def hpc_job_logs(run_id: str, tail: int = 200) -> str:
    """Return the last *tail* lines from a background job's log."""
    from hpc_pilot.jobs import get_job_logs

    return get_job_logs(run_id, tail=tail)
