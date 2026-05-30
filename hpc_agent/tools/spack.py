"""Spack software-stack query tools. See spec 06."""

from __future__ import annotations

from pydantic import BaseModel

from hpc_agent.config.settings import settings
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandSpec, run_command
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.diff import Diff
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.base import Risk, get_tool, tool
from hpc_agent.tools.errors import ErrorKind, ToolError
from hpc_agent.tools.result import ToolResult

SPACK = f"{settings.spack_root}/bin/spack"


class ListEnvsIn(BaseModel):
    pass


class FindIn(BaseModel):
    env: str


class SpecIn(BaseModel):
    spec: str


def _blast_radius(inp: BaseModel) -> int:
    return 0


def _gate_read(
    *,
    tool_name: str,
    inp: BaseModel,
    actor: str,
    actor_role: Role,
    policy: PolicyEngine | None,
) -> tuple[str, ToolResult | None]:
    meta, _fn, _br = get_tool(tool_name)
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id
    diff = Diff(changes=[], blast_radius=_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return audit_id, ToolResult.denied(g.reason or "denied by policy")
    return audit_id, None


def _finish_read(audit_id: str, *, ok: bool) -> None:
    event = audit.get_event(audit_id)
    if event is None:
        return
    event.decision = "auto"
    event.result_status = "ok" if ok else "error"
    audit.commit_event(event)


def _parse_envs(stdout: str) -> list[str]:
    envs: list[str] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("==>"):
            continue
        envs.append(stripped.lstrip("* ").strip())
    return envs


def _parse_find(stdout: str) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.lower().startswith("==>"):
            continue
        parts = stripped.split()
        rows.append(
            {
                "hash": parts[0] if parts and parts[0].startswith("/") else None,
                "spec": " ".join(parts[1:]) if parts and parts[0].startswith("/") else stripped,
            }
        )
    return rows


@tool(name="spack.list_envs", risk=Risk.READ, domain="spack", blast_radius=_blast_radius)
def list_envs(
    inp: ListEnvsIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """List configured Spack environments."""
    audit_id, denied = _gate_read(
        tool_name="spack.list_envs",
        inp=inp,
        actor=actor,
        actor_role=actor_role,
        policy=policy,
    )
    if denied is not None:
        return denied
    res = run_command(
        CommandSpec(argv=[SPACK, "env", "list"], timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack env list failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check SPACK_ROOT and Spack installation",
            )
        )
    _finish_read(audit_id, ok=True)
    return ToolResult.success(data={"envs": _parse_envs(res.stdout)}, audit_id=audit_id)


@tool(name="spack.find", risk=Risk.READ, domain="spack", blast_radius=_blast_radius)
def find(
    inp: FindIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """List installed specs in a Spack environment using parseable output."""
    audit_id, denied = _gate_read(
        tool_name="spack.find",
        inp=inp,
        actor=actor,
        actor_role=actor_role,
        policy=policy,
    )
    if denied is not None:
        return denied
    res = run_command(
        CommandSpec(argv=[SPACK, "-e", inp.env, "find", "-P"], timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack find failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check environment name and Spack installation",
            )
        )
    _finish_read(audit_id, ok=True)
    return ToolResult.success(
        data={"env": inp.env, "specs": _parse_find(res.stdout)},
        audit_id=audit_id,
    )


@tool(name="spack.spec", risk=Risk.READ, domain="spack", blast_radius=_blast_radius)
def spec(
    inp: SpecIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Preview Spack concretization for a spec without building it."""
    audit_id, denied = _gate_read(
        tool_name="spack.spec",
        inp=inp,
        actor=actor,
        actor_role=actor_role,
        policy=policy,
    )
    if denied is not None:
        return denied
    res = run_command(
        CommandSpec(argv=[SPACK, "spec", "-I", inp.spec], timeout_s=120),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack spec failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check spec syntax and Spack package availability",
            )
        )
    _finish_read(audit_id, ok=True)
    return ToolResult.success(
        data={"spec": inp.spec, "concretization": res.stdout},
        audit_id=audit_id,
    )
