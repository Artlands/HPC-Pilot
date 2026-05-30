"""Ansible configuration-management tools. See spec 04."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from hpc_agent.config.settings import settings
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandSpec, run_command
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.diff import Change, Diff
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.state.db import session_scope
from hpc_agent.state.models import Node, NodeRole
from hpc_agent.state.repos import NodeRepo
from hpc_agent.tools.base import Risk, get_tool, tool
from hpc_agent.tools.errors import ErrorKind, ToolError
from hpc_agent.tools.result import ToolResult

ANSIBLE_LINT = "ansible-lint"
ANSIBLE_PLAYBOOK = "ansible-playbook"


class LintPlaybookIn(BaseModel):
    playbook: str


class ComposePlaybookIn(BaseModel):
    name: str
    target_group: str
    roles: list[str]
    vars: dict[str, object] = {}
    dry_run: bool = True


class ManageInventoryIn(BaseModel):
    regenerate: bool = True
    dry_run: bool = True


class RunPlaybookIn(BaseModel):
    playbook: str
    limit: str | None = None
    extra_vars: dict[str, object] = {}
    dry_run: bool = True


class ManageSecretIn(BaseModel):
    ref: str


def _resolve_playbook(playbook: str) -> Path:
    path = Path(playbook)
    if path.is_absolute():
        return path
    return Path(settings.ansible_dir) / "playbooks" / playbook


def _role_dir(role: str) -> Path:
    return Path(settings.ansible_dir) / "roles" / role


def _playbook_path(name: str) -> Path:
    return Path(settings.ansible_dir) / "playbooks" / f"{name}.yml"


def _inventory_path() -> Path:
    return Path(settings.ansible_dir) / "inventory" / "hosts.yml"


def _secret_path(ref: str) -> Path:
    return Path(settings.ansible_dir) / "secrets" / ref


def _validate_secret_ref(ref: str) -> ToolError | None:
    path = Path(ref)
    if ref in {"", ".", ".."} or path.is_absolute() or ".." in path.parts:
        return ToolError(
            kind=ErrorKind.PRECONDITION,
            message="secret ref must be relative to the managed secret store",
            remediation="use a ref like 'munge/key'",
        )
    return None


def _role_allowed_vars(role: str) -> set[str]:
    spec_path = _role_dir(role) / "meta" / "argument_specs.yml"
    if not spec_path.exists():
        return set()
    raw = yaml.safe_load(spec_path.read_text()) or {}
    if not isinstance(raw, dict):
        return set()
    argument_specs = raw.get("argument_specs")
    if not isinstance(argument_specs, dict):
        return set()
    allowed: set[str] = set()
    for spec in argument_specs.values():
        if not isinstance(spec, dict):
            continue
        options = spec.get("options")
        if isinstance(options, dict):
            allowed.update(str(name) for name in options)
    return allowed


def _render_playbook(inp: ComposePlaybookIn) -> str:
    doc: list[dict[str, Any]] = [
        {
            "hosts": inp.target_group,
            "become": True,
            "vars": inp.vars,
            "roles": inp.roles,
        }
    ]
    return yaml.safe_dump(doc, sort_keys=False)


def _node_role_group(role: NodeRole) -> str:
    return role.value


def _node_hostvars(node: Node) -> dict[str, object]:
    hostvars: dict[str, object] = {}
    if node.ip:
        hostvars["ansible_host"] = node.ip
    if node.features:
        hostvars["features"] = node.features
    if node.gpu_count:
        hostvars["gpu_count"] = node.gpu_count
    if node.gpu_model:
        hostvars["gpu_model"] = node.gpu_model
    if node.cpu_count:
        hostvars["cpu_count"] = node.cpu_count
    if node.mem_mb:
        hostvars["mem_mb"] = node.mem_mb
    return hostvars


def _render_inventory(nodes: list[Node]) -> str:
    children: dict[str, object] = {}
    for role in NodeRole:
        hosts: dict[str, object] = {}
        for node in sorted(nodes, key=lambda n: n.hostname):
            if node.role == role:
                hosts[node.hostname] = _node_hostvars(node)
        if hosts:
            children[_node_role_group(role)] = {"hosts": hosts}
    doc = {"all": {"children": children}}
    return yaml.safe_dump(doc, sort_keys=True)


def _validate_playbook_name(name: str) -> ToolError | None:
    if Path(name).name != name or name in {"", ".", ".."}:
        return ToolError(
            kind=ErrorKind.PRECONDITION,
            message="playbook name must be a simple file stem",
            remediation="use a name like 'gpu-node-base'",
        )
    return None


def _validate_roles_and_vars(inp: ComposePlaybookIn) -> ToolError | None:
    if not inp.roles:
        return ToolError(kind=ErrorKind.PRECONDITION, message="at least one role is required")
    allowed_vars: set[str] = set()
    for role in inp.roles:
        if Path(role).name != role or role in {"", ".", ".."}:
            return ToolError(kind=ErrorKind.PRECONDITION, message=f"invalid role name '{role}'")
        if not _role_dir(role).is_dir():
            return ToolError(
                kind=ErrorKind.PRECONDITION,
                message=f"role '{role}' does not exist",
                remediation="add the role to the curated roles directory first",
            )
        allowed_vars.update(_role_allowed_vars(role))
    unknown_vars = set(inp.vars) - allowed_vars if allowed_vars else set(inp.vars)
    if unknown_vars:
        return ToolError(
            kind=ErrorKind.PRECONDITION,
            message=f"unknown role vars: {', '.join(sorted(unknown_vars))}",
            remediation="declare vars in the role meta/argument_specs.yml",
        )
    return None


def _blast_radius(inp: BaseModel) -> int:
    return 0


def _playbook_argv(inp: RunPlaybookIn, *, check: bool) -> list[str]:
    argv = [ANSIBLE_PLAYBOOK, str(_resolve_playbook(inp.playbook))]
    if inp.limit is not None:
        argv.extend(["--limit", inp.limit])
    if inp.extra_vars:
        argv.extend(["--extra-vars", yaml.safe_dump(inp.extra_vars, sort_keys=True)])
    if check:
        argv.extend(["--check", "--diff"])
    return argv


def _parse_playbook_summary(stdout: str) -> dict[str, object]:
    try:
        data = yaml.safe_load(stdout) or {}
    except yaml.YAMLError:
        return {"changed_hosts": [], "ok": 0, "changed": 0, "failed": 0, "unreachable": 0}
    if not isinstance(data, dict):
        return {"changed_hosts": [], "ok": 0, "changed": 0, "failed": 0, "unreachable": 0}
    stats = data.get("stats")
    if not isinstance(stats, dict):
        return {"changed_hosts": [], "ok": 0, "changed": 0, "failed": 0, "unreachable": 0}
    changed_hosts: list[str] = []
    summary = {"ok": 0, "changed": 0, "failed": 0, "unreachable": 0}
    for host, values in stats.items():
        if not isinstance(values, dict):
            continue
        changed = int(values.get("changed") or 0)
        if changed:
            changed_hosts.append(str(host))
        for key in summary:
            summary[key] += int(values.get(key) or 0)
    return {"changed_hosts": changed_hosts, **summary}


@tool(name="ansible.compose_playbook", risk=Risk.LOW, domain="ansible", blast_radius=_blast_radius)
def compose_playbook(
    inp: ComposePlaybookIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Render a playbook from curated roles without applying it."""
    meta, _fn, _br = get_tool("ansible.compose_playbook")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    for maybe_error in (_validate_playbook_name(inp.name), _validate_roles_and_vars(inp)):
        if maybe_error is not None:
            event.decision = "error"
            event.result_status = "error"
            audit.commit_event(event)
            return ToolResult.failed(maybe_error)

    path = _playbook_path(inp.name)
    rendered = _render_playbook(inp)
    before = path.read_text() if path.exists() else None
    changes = [] if before == rendered else [Change(target=str(path), op="modify", after=rendered)]
    diff = Diff(
        changes=changes,
        config_diff=rendered if before is None else None,
        blast_radius=_blast_radius(inp),
        reversible=True,
    )
    if diff.is_noop():
        event.decision = "auto"
        event.diff_summary = "no-op"
        event.result_status = "ok"
        audit.commit_event(event)
        return ToolResult.success(
            data={"playbook_path": str(path), "resolved_roles": inp.roles, "noop": True},
            audit_id=audit_id,
        )

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="compose"
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

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered)
    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"playbook_path": str(path), "resolved_roles": inp.roles},
        diff=diff,
        audit_id=audit_id,
    )


@tool(name="ansible.manage_inventory", risk=Risk.LOW, domain="ansible", blast_radius=_blast_radius)
def manage_inventory(
    inp: ManageInventoryIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Generate Ansible inventory from the state-store node source of truth."""
    meta, _fn, _br = get_tool("ansible.manage_inventory")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        with session_scope() as session:
            nodes = NodeRepo(session).all()
            rendered = _render_inventory(nodes)
    except Exception as exc:  # noqa: BLE001 - return structured tool error at the boundary
        event.decision = "error"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.PRECONDITION,
                message="could not read nodes from state store",
                detail=str(exc),
                remediation="configure the state database and run migrations first",
            )
        )

    path = _inventory_path()
    before = path.read_text() if path.exists() else None
    changes = [] if before == rendered else [Change(target=str(path), op="modify", after=rendered)]
    diff = Diff(
        changes=changes,
        config_diff=rendered if before is None else None,
        blast_radius=_blast_radius(inp),
        reversible=True,
    )
    if diff.is_noop():
        event.decision = "auto"
        event.diff_summary = "no-op"
        event.result_status = "ok"
        audit.commit_event(event)
        return ToolResult.success(
            data={"inventory_path": str(path), "noop": True},
            audit_id=audit_id,
        )

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="regenerate"
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

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered)
    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"inventory_path": str(path), "hosts": len(nodes)},
        diff=diff,
        audit_id=audit_id,
    )


@tool(name="ansible.manage_secret", risk=Risk.READ, domain="ansible", blast_radius=_blast_radius)
def manage_secret(
    inp: ManageSecretIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Check that a secret reference exists without reading or returning secret material."""
    meta, _fn, _br = get_tool("ansible.manage_secret")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id

    error = _validate_secret_ref(inp.ref)
    if error is not None:
        event.decision = "error"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(error)

    diff = Diff(changes=[], blast_radius=_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    path = _secret_path(inp.ref)
    if not path.is_file():
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.NOT_FOUND,
                message=f"secret ref '{inp.ref}' is not present",
                remediation="provision the secret in the configured secret backend",
            )
        )

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(data={"ref": inp.ref, "present": True}, audit_id=audit_id)


@tool(name="ansible.lint_playbook", risk=Risk.READ, domain="ansible", blast_radius=_blast_radius)
def lint_playbook(
    inp: LintPlaybookIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Run ansible-lint and ansible-playbook --syntax-check for a playbook."""
    meta, _fn, _br = get_tool("ansible.lint_playbook")
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
        return ToolResult.denied(g.reason or "denied by policy")

    playbook = _resolve_playbook(inp.playbook)
    if not playbook.exists():
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.PRECONDITION,
                message=f"playbook '{playbook}' does not exist",
                remediation="compose or provide the playbook first",
            )
        )

    lint = run_command(
        CommandSpec(argv=[ANSIBLE_LINT, str(playbook)], timeout_s=120),
        actor=actor,
        audit_id=audit_id,
    )
    if lint.rc != 0:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"ansible-lint failed (rc={lint.rc})",
                detail=lint.stdout + lint.stderr,
                remediation="fix lint errors before applying the playbook",
            )
        )

    syntax = run_command(
        CommandSpec(argv=[ANSIBLE_PLAYBOOK, "--syntax-check", str(playbook)], timeout_s=120),
        actor=actor,
        audit_id=audit_id,
    )
    if syntax.rc != 0:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"ansible-playbook --syntax-check failed (rc={syntax.rc})",
                detail=syntax.stdout + syntax.stderr,
                remediation="fix playbook syntax before applying",
            )
        )

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"playbook": str(playbook), "lint": "passed", "syntax": "passed"},
        audit_id=audit_id,
    )


@tool(name="ansible.run_playbook", risk=Risk.MEDIUM, domain="ansible", blast_radius=_blast_radius)
def run_playbook(
    inp: RunPlaybookIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Run an Ansible playbook, using --check --diff for dry-run previews."""
    meta, _fn, _br = get_tool("ansible.run_playbook")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    lint = lint_playbook(
        LintPlaybookIn(playbook=inp.playbook),
        actor=actor,
        actor_role=actor_role,
        policy=policy,
    )
    if not lint.ok:
        event.decision = "error"
        event.result_status = "error"
        audit.commit_event(event)
        return lint

    preview_argv = _playbook_argv(inp, check=True)
    preview = run_command(
        CommandSpec(argv=preview_argv, timeout_s=600),
        actor=actor,
        audit_id=audit_id,
    )
    if preview.rc != 0:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"ansible-playbook dry-run failed (rc={preview.rc})",
                detail=preview.stdout + preview.stderr,
                remediation="fix playbook or inventory before applying",
            )
        )

    summary = _parse_playbook_summary(preview.stdout)
    raw_changed_hosts = summary.get("changed_hosts")
    changed_hosts = raw_changed_hosts if isinstance(raw_changed_hosts, list) else []
    changes = [
        Change(target=f"host/{host}", op="modify", after="changed")
        for host in changed_hosts
        if isinstance(host, str)
    ]
    diff = Diff(
        changes=changes,
        config_diff=preview.stdout or None,
        commands_preview=[preview_argv],
        blast_radius=len(changed_hosts),
        reversible=False,
        revert_hint="re-apply the prior config commit or a corrective playbook",
    )

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="run"
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

    apply = run_command(
        CommandSpec(argv=_playbook_argv(inp, check=False), timeout_s=600),
        actor=actor,
        audit_id=audit_id,
    )
    if apply.rc != 0:
        event.decision = _decision_str(g)
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"ansible-playbook apply failed (rc={apply.rc})",
                detail=apply.stdout + apply.stderr,
                remediation="inspect failed hosts and re-run after fixing",
            )
        )

    event.decision = _decision_str(g)
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data=_parse_playbook_summary(apply.stdout),
        diff=diff,
        audit_id=audit_id,
    )


def _decision_str(g: safety_gate.Gate) -> str:
    if g.approved:
        return f"approved-by:{g.approver or 'unknown'}"
    return "auto"
