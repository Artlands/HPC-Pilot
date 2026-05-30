"""The single chokepoint for all shell execution. See spec 00 §4.

No tool calls subprocess directly. run_command enforces a binary allowlist, never uses
shell=True, redacts secrets in logs, enforces a timeout, and NEVER raises on nonzero rc.
"""

from __future__ import annotations

import shutil
import subprocess
import time

from pydantic import BaseModel

# Binaries the agent is permitted to execute. Anything else is rejected.
ALLOWLIST: frozenset[str] = frozenset(
    {
        "wwctl",
        "sacctmgr",
        "scontrol",
        "sinfo",
        "squeue",
        "sacct",
        "sreport",
        "sdiag",
        "slurmctld",
        "ansible",
        "ansible-playbook",
        "ansible-lint",
        "spack",
        "munge",
        "git",
        "true",  # used by tests
        "echo",  # used by tests
    }
)


class CommandSpec(BaseModel):
    argv: list[str]  # never a shell string; no shell=True
    cwd: str | None = None
    timeout_s: int = 120
    input_text: str | None = None
    redact: list[str] = []  # substrings to mask in logs (secrets)


class CommandResult(BaseModel):
    rc: int
    stdout: str
    stderr: str
    duration_s: float


class CommandRejected(Exception):
    """Binary not on the allowlist."""


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def redacted_argv(spec: CommandSpec) -> list[str]:
    """argv with secrets masked, for logging/audit/diff preview."""
    return [_redact(a, spec.redact) for a in spec.argv]


def run_command(spec: CommandSpec, *, actor: str, audit_id: str) -> CommandResult:
    if not spec.argv:
        raise CommandRejected("empty argv")
    binary = spec.argv[0]
    base = binary.rsplit("/", 1)[-1]
    if base not in ALLOWLIST:
        raise CommandRejected(f"binary not allowed: {base}")

    resolved = shutil.which(base) or binary

    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, shell=False, allowlisted
            [resolved, *spec.argv[1:]],
            cwd=spec.cwd,
            input=spec.input_text,
            capture_output=True,
            text=True,
            timeout=spec.timeout_s,
            shell=False,
            check=False,
        )
        duration = time.monotonic() - start
        result = CommandResult(
            rc=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration_s=round(duration, 3),
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - start
        result = CommandResult(
            rc=124,
            stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
            stderr=f"timeout after {spec.timeout_s}s",
            duration_s=round(duration, 3),
        )

    # Audit-log the redacted command. Import here to avoid a cycle at module load.
    from hpc_agent.exec.audit import record_command

    record_command(
        audit_id=audit_id,
        actor=actor,
        argv=redacted_argv(spec),
        rc=result.rc,
        duration_s=result.duration_s,
    )
    return result
