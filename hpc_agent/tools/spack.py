"""Spack software-stack query tools. See spec 06."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from hpc_agent.config.settings import settings as settings
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandSpec, redacted_argv, run_command
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
    gate_override: safety_gate.Gate | None = None,
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
    gate_override: safety_gate.Gate | None = None,
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
    gate_override: safety_gate.Gate | None = None,
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


# --- Spack environment management tools (spec 06 §1.1) ---


class ManageEnvIn(BaseModel):
    name: str
    op: Literal["create", "add_specs", "remove_specs"]
    specs: list[str] = []
    dry_run: bool = True


class BuildcacheIn(BaseModel):
    op: Literal["push", "update_index", "add_mirror"]
    mirror: str
    env: str | None = None
    signing_key_ref: str | None = None
    dry_run: bool = True


class ManageCompilersIn(BaseModel):
    op: Literal["find", "add"]
    path: str | None = None
    scope: Literal["site", "env"] = "site"
    env: str | None = None
    dry_run: bool = True


class GenModulesIn(BaseModel):
    env: str
    module_type: Literal["lmod", "tcl"] = "lmod"
    dry_run: bool = True


class CreateViewIn(BaseModel):
    env: str
    prefix: str | None = None
    dry_run: bool = True


class InstallIn(BaseModel):
    env: str
    use_buildcache: bool = True
    jobs: int = 16
    dry_run: bool = True


def _blast_radius_env(inp: BaseModel) -> int:
    return 1


def _blast_radius_buildcache(inp: BaseModel) -> int:
    return 1


def _blast_radius_compilers(inp: BaseModel) -> int:
    return 1


def _blast_radius_modules(inp: BaseModel) -> int:
    return 1


def _blast_radius_view(inp: BaseModel) -> int:
    return 1


def _blast_radius_install(inp: BaseModel) -> int:
    return 1


def _build_manage_env_argv(inp: ManageEnvIn) -> list[str]:
    argv = [SPACK, "env", "edit", inp.name]
    if inp.op == "create":
        argv = [SPACK, "env", "create", inp.name]
    return argv


def _build_buildcache_argv(inp: BuildcacheIn) -> list[str]:
    if inp.op == "push":
        argv = [SPACK, "buildcache", "push", inp.mirror]
        if inp.env:
            argv.extend(["-e", inp.env])
    elif inp.op == "update_index":
        argv = [SPACK, "buildcache", "update-index", inp.mirror]
    else:
        name = inp.mirror.replace("/", "-").replace(":", "-").strip("-") or "mirror"
        argv = [SPACK, "mirror", "add", name, inp.mirror]
    return argv


def _build_manage_compilers_argv(inp: ManageCompilersIn) -> list[str]:
    argv = [SPACK, "compiler"]
    if inp.op == "find":
        argv.extend(["find", "--scope", inp.scope])
        if inp.path is not None:
            argv.append(inp.path)
    else:
        argv.extend(["add", "--scope", inp.scope])
        if inp.path is not None:
            argv.append(inp.path)
    return argv


def _build_gen_modules_argv(inp: GenModulesIn) -> list[str]:
    module_cmd = "lmod" if inp.module_type == "lmod" else "tcl"
    return [SPACK, "-e", inp.env, "module", module_cmd, "refresh", "--delete-tree", "-y"]


def _build_create_view_argv(inp: CreateViewIn) -> list[str]:
    if inp.prefix is not None:
        return [SPACK, "-e", inp.env, "view", "symlink", inp.prefix]
    return [SPACK, "-e", inp.env, "env", "view", "enable"]


def _build_install_argv(inp: InstallIn) -> list[str]:
    argv = [SPACK, "-e", inp.env, "install"]
    if inp.use_buildcache:
        argv.append("--use-buildcache")
    argv.extend(["-j", str(inp.jobs)])
    if inp.dry_run:
        argv.append("--fake")
    return argv


def _finish_read(audit_id: str, *, ok: bool) -> None:
    event = audit.get_event(audit_id)
    if event is None:
        return
    event.decision = "auto"
    event.result_status = "ok" if ok else "error"
    audit.commit_event(event)


# --- Tools ---


@tool(
    name="spack.manage_environment",
    risk=Risk.LOW,
    domain="spack",
    blast_radius=_blast_radius_env,
)
def manage_environment(
    inp: ManageEnvIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Create or modify a Spack environment."""
    meta, _fn, _br = get_tool("spack.manage_environment")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    forward_argv = _build_manage_env_argv(inp)
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius_env(inp),
        reversible=True,
    )

    if inp.op == "create":
        event.diff_summary = f"env create {inp.name}"
    elif inp.op == "add_specs":
        event.diff_summary = f"env edit {inp.name} (add {len(inp.specs)} specs)"
    else:
        event.diff_summary = f"env edit {inp.name} (remove {len(inp.specs)} specs)"

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op=inp.op
    )
    if g.denied:
        event.decision = "denied"
        event.diff_summary = diff.render()
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    if inp.dry_run:
        event.decision = "dry_run"
        event.diff_summary = diff.render()
        event.result_status = "dry_run"
        audit.commit_event(event)
        return ToolResult.dry_run(diff)

    if g.requires_approval and not g.approved:
        event.decision = "needs_approval"
        event.diff_summary = diff.render()
        event.result_status = "needs_approval"
        audit.commit_event(event)
        return ToolResult.needs_approval(diff)

    res = run_command(
        CommandSpec(argv=forward_argv, timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack env {inp.op} failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check Spack installation and environment name",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"env": inp.name, "op": inp.op},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="spack.manage_buildcache",
    risk=Risk.MEDIUM,
    domain="spack",
    blast_radius=_blast_radius_buildcache,
)
def manage_buildcache(
    inp: BuildcacheIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Manage Spack buildcache (push/update/add mirror)."""
    meta, _fn, _br = get_tool("spack.manage_buildcache")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run", "signing_key_ref"}),
    )
    audit_id = event.id

    forward_argv = _build_buildcache_argv(inp)
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius_buildcache(inp),
        reversible=False,
    )

    key_hint = f" (key: {inp.signing_key_ref})" if inp.signing_key_ref else ""
    event.diff_summary = f"buildcache {inp.op} {inp.mirror}{key_hint}"

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op=inp.op
    )
    if g.denied:
        event.decision = "denied"
        event.diff_summary = diff.render()
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    if inp.dry_run:
        event.decision = "dry_run"
        event.diff_summary = diff.render()
        event.result_status = "dry_run"
        audit.commit_event(event)
        return ToolResult.dry_run(diff)

    res = run_command(
        CommandSpec(argv=forward_argv, timeout_s=180),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack buildcache {inp.op} failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check mirror URL and Spack permissions",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"op": inp.op, "mirror": inp.mirror},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="spack.manage_compilers",
    risk=Risk.LOW,
    domain="spack",
    blast_radius=_blast_radius_compilers,
)
def manage_compilers(
    inp: ManageCompilersIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Find or add compilers to Spack."""
    meta, _fn, _br = get_tool("spack.manage_compilers")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    forward_argv = _build_manage_compilers_argv(inp)
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius_compilers(inp),
        reversible=True,
    )

    event.diff_summary = f"compiler {inp.op} --scope {inp.scope}"

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op=inp.op
    )
    if g.denied:
        event.decision = "denied"
        event.diff_summary = diff.render()
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    if inp.dry_run:
        event.decision = "dry_run"
        event.diff_summary = diff.render()
        event.result_status = "dry_run"
        audit.commit_event(event)
        return ToolResult.dry_run(diff)

    res = run_command(
        CommandSpec(argv=forward_argv, timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack compiler {inp.op} failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check Spack installation",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"op": inp.op, "scope": inp.scope, "path": inp.path},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="spack.generate_modules",
    risk=Risk.LOW,
    domain="spack",
    blast_radius=_blast_radius_modules,
)
def generate_modules(
    inp: GenModulesIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Generate Lmod/Tcl modulefiles for a Spack environment."""
    meta, _fn, _br = get_tool("spack.generate_modules")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    forward_argv = _build_gen_modules_argv(inp)
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius_modules(inp),
        reversible=True,
    )

    event.diff_summary = f"module {inp.module_type} refresh --env={inp.env}"

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.diff_summary = diff.render()
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    if inp.dry_run:
        event.decision = "dry_run"
        event.diff_summary = diff.render()
        event.result_status = "dry_run"
        audit.commit_event(event)
        return ToolResult.dry_run(diff)

    res = run_command(
        CommandSpec(argv=forward_argv, timeout_s=300),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack module {inp.module_type} refresh failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check Spack environment",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"env": inp.env, "module_type": inp.module_type},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="spack.create_view",
    risk=Risk.LOW,
    domain="spack",
    blast_radius=_blast_radius_view,
)
def create_view(
    inp: CreateViewIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Create or enable a filesystem view for a Spack environment."""
    meta, _fn, _br = get_tool("spack.create_view")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    forward_argv = _build_create_view_argv(inp)
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius_view(inp),
        reversible=True,
    )

    prefix_hint = inp.prefix if inp.prefix else "default view"
    event.diff_summary = f"view enable --env={inp.env} {prefix_hint}"

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="create"
    )
    if g.denied:
        event.decision = "denied"
        event.diff_summary = diff.render()
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    if inp.dry_run:
        event.decision = "dry_run"
        event.diff_summary = diff.render()
        event.result_status = "dry_run"
        audit.commit_event(event)
        return ToolResult.dry_run(diff)

    res = run_command(
        CommandSpec(argv=forward_argv, timeout_s=120),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack view enable failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check Spack environment and filesystem permissions",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"env": inp.env, "prefix": inp.prefix},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="spack.install_packages",
    risk=Risk.MEDIUM,
    domain="spack",
    blast_radius=_blast_radius_install,
)
def install_packages(
    inp: InstallIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Install packages in a Spack environment."""
    meta, _fn, _br = get_tool("spack.install_packages")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    forward_argv = _build_install_argv(inp)
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius_install(inp),
        reversible=False,
    )

    event.diff_summary = f"install --env={inp.env} -j{inp.jobs}"

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="install"
    )
    if g.denied:
        event.decision = "denied"
        event.diff_summary = diff.render()
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    if inp.dry_run:
        event.decision = "dry_run"
        event.diff_summary = diff.render()
        event.result_status = "dry_run"
        audit.commit_event(event)
        return ToolResult.dry_run(diff)

    res = run_command(
        CommandSpec(argv=forward_argv, timeout_s=21600),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"spack install failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check spec resolution and build dependencies",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"env": inp.env, "jobs": inp.jobs},
        diff=diff,
        audit_id=audit_id,
    )
