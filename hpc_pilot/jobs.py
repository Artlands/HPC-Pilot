"""Async job table for long-running HPC operations (Spack installs, Ansible runs).

Jobs are persisted to ``~/.hpc-pilot/jobs/<run_id>.json`` so they survive restarts.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from hpc_pilot.paths import jobs_dir


@dataclass
class JobRecord:
    run_id: str
    cmd: str
    started_at: float
    pid: int | None
    status: str  # "running" | "completed" | "failed"
    returncode: int | None = None
    log_path: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _job_path(run_id: str) -> str:
    return os.path.join(jobs_dir(), f"{run_id}.json")


def generate_run_id() -> str:
    """Return a hex UUID4 suitable as a run identifier."""
    return uuid.uuid4().hex


def _save_record(record: JobRecord) -> None:
    os.makedirs(os.path.dirname(_job_path(record.run_id)), exist_ok=True)
    with open(_job_path(record.run_id), "w") as f:
        json.dump(record.__dict__, f, indent=2, default=str)


def _load_record(run_id: str) -> JobRecord | None:
    path = _job_path(run_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return JobRecord(**data)
    except (json.JSONDecodeError, OSError, TypeError):
        return None


def start_job(cmd: list[str], log_path: str | None = None, meta: dict[str, Any] | None = None) -> JobRecord:
    """Launch *cmd* as a background subprocess and persist a JobRecord.

    The process writes stdout + stderr to *log_path* (auto-generated if None).
    Returns immediately with a JobRecord in "running" status.
    """
    import time

    run_id = generate_run_id()
    if log_path is None:
        os.makedirs(jobs_dir(), exist_ok=True)
        log_path = os.path.join(jobs_dir(), f"{run_id}.log")

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    log_file = open(log_path, "w")

    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
    )

    record = JobRecord(
        run_id=run_id,
        cmd=" ".join(cmd),
        started_at=time.time(),
        pid=process.pid,
        status="running",
        log_path=log_path,
        meta=meta or {},
    )
    _save_record(record)

    def _poll(proc: subprocess.Popen[str], rec: JobRecord, lf) -> None:
        proc.wait()
        lf.close()
        rec.returncode = proc.returncode
        rec.status = "completed" if proc.returncode == 0 else "failed"
        if proc.returncode != 0:
            rec.error = f"exit code {proc.returncode}"
        _save_record(rec)

    threading.Thread(target=_poll, args=(process, record, log_file), daemon=True).start()
    return record


def get_job(run_id: str) -> JobRecord | None:
    """Return the JobRecord for *run_id*, or None."""
    return _load_record(run_id)


def get_job_logs(run_id: str, tail: int = 200) -> str:
    """Return the last *tail* lines from the job's log file."""
    record = _load_record(run_id)
    if record is None or not record.log_path:
        return f"[job {run_id} not found]"
    if not os.path.exists(record.log_path):
        return "[log file not found]"
    with open(record.log_path) as f:
        lines = f.readlines()
    return "".join(lines[-tail:])


def list_jobs() -> list[dict[str, Any]]:
    """Return all job records sorted newest-first."""
    jdir = jobs_dir()
    if not os.path.isdir(jdir):
        return []
    records: list[dict[str, Any]] = []
    for fname in os.listdir(jdir):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(jdir, fname)
        try:
            with open(path) as f:
                records.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    records.sort(key=lambda r: r.get("started_at", 0), reverse=True)
    return records
