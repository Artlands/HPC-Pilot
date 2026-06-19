"""Job-management tools exposed to the agent."""
from __future__ import annotations

from typing import Any


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


def hpc_job_logs(run_id: str, tail: int = 200) -> str:
    """Return the last *tail* lines from a background job's log."""
    from hpc_pilot.jobs import get_job_logs

    return get_job_logs(run_id, tail=tail)
