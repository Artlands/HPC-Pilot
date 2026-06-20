"""Job-management tools exposed to the agent."""

from __future__ import annotations

import io
import os
import tempfile
from typing import Any

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _NAME_RE, _validate


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


# ===================================================================
# Job step resource metrics (S14) — moved from system.py
# ===================================================================


@hpc_tool(
    name="hpc_slurm_job_step_metrics",
    role=Role.VIEWER,
    schema={
        "name": "hpc_slurm_job_step_metrics",
        "description": "Retrieve per-step resource metrics for a completed job via sacct. Returns CPU, memory, wall time per job step.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "Slurm job ID"}},
            "required": ["job_id"],
        },
    },
)
def hpc_slurm_job_step_metrics(
    job_id: str,
    *,
    cluster: str = "default",
) -> str:
    """Retrieve per-step resource metrics for a completed job via ``sacct``.

    Returns a formatted table with each job step's CPU, memory, and
    wall time usage.

    Args:
        job_id: Slurm job ID (e.g. ``481516``).
    """
    _validate(job_id, "job_id", _NAME_RE)
    cl = _resolve_cluster(cluster)

    raw = _run(
        [
            cl.slurm("sacct"),
            "-j",
            job_id,
            "-P",
            "--format=JobID,JobName,State,ExitCode,Elapsed,AllocCPUS,ReqMem,MaxRSS,MaxVMSize,NNodes",
        ],
        cluster=cl,
        timeout=30,
    )

    out = io.StringIO()
    out.write(
        f"{'JobID':18} {'State':12} {'Elapsed':12} {'CPUS':>5} {'MaxRSS':>12} {'MaxVM':>12} {'Nodes':>5}\n"
    )
    out.write("-" * 80 + "\n")

    lines = raw.strip().splitlines()
    for i, line in enumerate(lines):
        parts = line.split("|")
        if not parts or not parts[0].strip():
            continue
        if i == 0 and parts[0].strip().upper() == "JOBID":
            continue  # skip header
        if len(parts) >= 8:
            step_id = parts[0][:18]
            state = parts[2][:12]
            elapsed = parts[4][:12]
            cpus = parts[5][:5]
            maxrss = parts[7][:12]
            maxvm = parts[8][:12]
            nodes = parts[9][:5]
            out.write(
                f"{step_id:18} {state:12} {elapsed:12} {cpus:>5} {maxrss:>12} {maxvm:>12} {nodes:>5}\n"
            )

    return out.getvalue().rstrip()


# ===================================================================
# Test job submission (S3) — moved from system.py
# ===================================================================


@hpc_tool(
    name="hpc_job_submit_test",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_job_submit_test",
        "description": "Submit a short validation test job via sbatch. Use after provisioning or image builds to verify the cluster works.",
        "input_schema": {
            "type": "object",
            "properties": {
                "partition": {"type": "string", "description": "Partition to submit to"},
                "num_nodes": {"type": "integer", "description": "Number of nodes (default 1)"},
                "ntasks": {"type": "integer", "description": "Number of tasks (default 1)"},
            },
            "required": [],
        },
    },
)
def hpc_job_submit_test(
    partition: str = "",
    num_nodes: int = 1,
    ntasks: int = 1,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Submit a short validation/test job via ``sbatch`` and return the job ID.

    Creates a minimal test script that runs ``hostname`` and sleeps 10 seconds.
    Use after provisioning or image builds to verify the cluster is working.

    Args:
        partition: Partition to submit to (optional).
        num_nodes: Number of nodes requested (default 1).
        ntasks: Number of tasks (default 1).
        dry_run: Preview the job script without submitting.
    """
    cl = _resolve_cluster(cluster)

    script = "#!/bin/bash\n"
    script += f"#SBATCH --nodes={num_nodes}\n"
    script += f"#SBATCH --ntasks={ntasks}\n"
    script += "#SBATCH --time=00:05:00\n"
    script += "#SBATCH --job-name=hpc-pilot-validate\n"
    script += "#SBATCH --output=hpc-pilot-validate-%j.out\n"
    if partition:
        script += f"#SBATCH --partition={partition}\n"
    script += '\necho "Job started on $(hostname) at $(date)"\n'
    script += 'echo "SLURM_NODELIST=$SLURM_NODELIST"\n'
    script += "sleep 10\n"
    script += 'echo "Job finished at $(date)"\n'

    if dry_run:
        return "DRY-RUN: would submit:\n" + script

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".sh", delete=False, prefix="hpc-validate-"
    ) as f:
        f.write(script)
        script_path = f.name

    try:
        result = _run([cl.slurm("sbatch"), script_path], cluster=cl, timeout=30)
        return result.strip()
    finally:
        os.unlink(script_path)
