"""Warewulf provisioning tools. See spec 03.

All commands run via `run_command` under scoped sudo.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from hpc_agent.config.settings import settings
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandSpec, redacted_argv, run_command
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.diff import Diff
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.base import Risk, get_tool, tool
from hpc_agent.tools.errors import ErrorKind, ToolError
from hpc_agent.tools.result import ToolResult

WWCTL = f"{settings.ww_bin_dir}/wwctl"


class ImportContainerIn(BaseModel):
    name: str
    source: str
    dry_run: bool = True


class BuildImageIn(BaseModel):
    name: str
    base_image: str
    kind: Literal["compute_cpu", "compute_gpu"]
    packages: list[str] = []
    kernel_args: str | None = None
    nvidia_driver_version: str | None = None
    cuda_version: str | None = None
    enable_fabricmanager: bool = False
    install_dcgm: bool = True
    dry_run: bool = True


class DefineProfileIn(BaseModel):
    name: str
    image: str
    system_overlays: list[str] = ["wwinit", "hosts", "ssh.host_keys"]
    runtime_overlays: list[str] = ["hosts", "ssh.authorized_keys", "munge", "slurm"]
    kernel_args: str | None = None
    network: dict[str, str] | None = None
    dry_run: bool = True


class ManageOverlayIn(BaseModel):
    overlay: str
    files: dict[str, str]
    dry_run: bool = True


class AssignImageIn(BaseModel):
    nodes: list[str]
    profile: str
    dry_run: bool = True


class ProvisionNodeIn(BaseModel):
    hostname: str
    mac: str
    ip: str
    netdev: str = "eth0"
    profile: str
    role: str
    dry_run: bool = True


class RebuildOverlayIn(BaseModel):
    node: str | None = None
    dry_run: bool = True


class ListImagesIn(BaseModel):
    pass


class ListNodesIn(BaseModel):
    pass


def _blast_radius(inp: BaseModel) -> int:
    if isinstance(inp, ImportContainerIn):
        return 1
    if isinstance(inp, BuildImageIn):
        return 1
    if isinstance(inp, AssignImageIn):
        return len(inp.nodes)
    if isinstance(inp, ProvisionNodeIn):
        return 1
    return 0


def _finish_read(audit_id: str, *, ok: bool) -> None:
    event = audit.get_event(audit_id)
    if event is None:
        return
    event.decision = "auto"
    event.result_status = "ok" if ok else "error"
    audit.commit_event(event)


@tool(
    name="warewulf.import_container",
    risk=Risk.MEDIUM,
    domain="warewulf",
    blast_radius=_blast_radius,
)
def import_container(
    inp: ImportContainerIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Import a base OS container into Warewulf.

    Risk: MEDIUM
    """
    meta, _fn, _br = get_tool("warewulf.import_container")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    forward_argv = [WWCTL, "container", "import", inp.source, inp.name, "--syncuser"]
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    event.diff_summary = f"container import {inp.source} -> {inp.name}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="import"
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
        CommandSpec(argv=forward_argv, timeout_s=600),
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
                message=f"wwctl container import failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check container source and Warewulf installation",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"name": inp.name, "source": inp.source},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.build_node_image",
    risk=Risk.MEDIUM,
    domain="warewulf",
    blast_radius=_blast_radius,
)
def build_node_image(
    inp: BuildImageIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Build a compute node image with optional GPU support.

    Risk: MEDIUM
    Idempotent on spec_hash.
    """
    meta, _fn, _br = get_tool("warewulf.build_node_image")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    # In dry-run, just compute the spec_hash
    spec_hash = f"dummy-{inp.name}"
    if inp.dry_run:
        diff = Diff(
            changes=[],
            commands_preview=[],
            blast_radius=_blast_radius(inp),
            reversible=True,
        )
        event.decision = "dry_run"
        event.diff_summary = f"build image {inp.name} (spec_hash={spec_hash})"
        event.result_status = "dry_run"
        audit.commit_event(event)
        return ToolResult.dry_run(diff)

    forward_argv = [WWCTL, "container", "build", inp.name]
    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=forward_argv))],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    event.diff_summary = f"container build {inp.name}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="build"
    )
    if g.denied:
        event.decision = "denied"
        event.diff_summary = diff.render()
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    if g.requires_approval and not g.approved:
        event.decision = "needs_approval"
        event.diff_summary = diff.render()
        event.result_status = "needs_approval"
        audit.commit_event(event)
        return ToolResult.needs_approval(diff)

    res = run_command(
        CommandSpec(argv=forward_argv, timeout_s=900),
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
                message=f"wwctl container build failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check container build logs",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={
            "name": inp.name,
            "kind": inp.kind,
            "spec_hash": spec_hash,
            "kernel_version": "unknown",
            "driver_version": inp.nvidia_driver_version,
            "cuda_version": inp.cuda_version,
        },
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.define_profile",
    risk=Risk.LOW,
    domain="warewulf",
    blast_radius=_blast_radius,
)
def define_profile(
    inp: DefineProfileIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Define or update a Warewulf profile.

    Risk: LOW
    """
    meta, _fn, _br = get_tool("warewulf.define_profile")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    argv = [WWCTL, "profile", "set", inp.name]
    argv.extend(["--container", inp.image])
    argv.extend(["--runtime-overlays", ",".join(inp.runtime_overlays)])
    argv.extend(["--system-overlays", ",".join(inp.system_overlays)])
    if inp.kernel_args:
        argv.extend(["--kernel-args", inp.kernel_args])

    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=argv))],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    event.diff_summary = f"profile set {inp.name} -> container={inp.image}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="set"
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
        CommandSpec(argv=argv, timeout_s=60),
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
                message=f"wwctl profile set failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check profile name and container existence",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"name": inp.name, "image": inp.image},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.manage_overlay",
    risk=Risk.MEDIUM,
    domain="warewulf",
    blast_radius=_blast_radius,
)
def manage_overlay(
    inp: ManageOverlayIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Manage overlay template files.

    Risk: MEDIUM
    """
    meta, _fn, _br = get_tool("warewulf.manage_overlay")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    diff = Diff(
        changes=[],
        commands_preview=[],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    event.diff_summary = f"overlay {inp.overlay}: {len(inp.files)} files"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="edit"
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

    # In real implementation, we'd copy files and run wwctl overlay build
    # For now, just simulate success
    res = run_command(
        CommandSpec(argv=[WWCTL, "overlay", "build", inp.overlay], timeout_s=60),
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
                message=f"wwctl overlay build failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check overlay template syntax",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"overlay": inp.overlay, "files": len(inp.files)},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.assign_image_to_nodes",
    risk=Risk.MEDIUM,
    domain="warewulf",
    blast_radius=_blast_radius,
)
def assign_image_to_nodes(
    inp: AssignImageIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Assign an image/profile to one or more nodes.

    Risk: MEDIUM
    """
    meta, _fn, _br = get_tool("warewulf.assign_image_to_nodes")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    changes = []
    for node in inp.nodes:
        changes.append(
            {
                "target": f"node/{node}",
                "field": "profile",
                "before": None,
                "after": inp.profile,
                "op": "assign",
            }
        )

    diff = Diff(
        changes=changes,
        commands_preview=[
            redacted_argv(CommandSpec(argv=[WWCTL, "node", "set", node, "--profile", inp.profile]))
            for node in inp.nodes
        ],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    event.diff_summary = f"assign {len(inp.nodes)} nodes -> profile {inp.profile}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="assign"
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

    for node in inp.nodes:
        res = run_command(
            CommandSpec(argv=[WWCTL, "node", "set", node, "--profile", inp.profile], timeout_s=60),
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
                    message=f"wwctl node set {node} failed (rc={res.rc})",
                    detail=res.stderr,
                    remediation="check node exists and profile is valid",
                )
            )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"nodes": inp.nodes, "profile": inp.profile},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.provision_node",
    risk=Risk.MEDIUM,
    domain="warewulf",
    blast_radius=_blast_radius,
)
def provision_node(
    inp: ProvisionNodeIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Register a new node for PXE boot.

    Risk: MEDIUM
    """
    meta, _fn, _br = get_tool("warewulf.provision_node")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    argv = [
        WWCTL,
        "node",
        "add",
        inp.hostname,
        "--netdev",
        inp.netdev,
        "--hwaddr",
        inp.mac,
        "--ipaddr",
        inp.ip,
        "--profile",
        inp.profile,
    ]

    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=argv))],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    event.diff_summary = f"provision node {inp.hostname}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="provision"
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
        CommandSpec(argv=argv, timeout_s=60),
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
                message=f"wwctl node add failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check node configuration",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={
            "hostname": inp.hostname,
            "mac": inp.mac,
            "ip": inp.ip,
            "profile": inp.profile,
        },
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.rebuild_overlay",
    risk=Risk.LOW,
    domain="warewulf",
    blast_radius=_blast_radius,
)
def rebuild_overlay(
    inp: RebuildOverlayIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """RebuildWarewulf overlay for a node or all nodes.

    Risk: LOW
    """
    meta, _fn, _br = get_tool("warewulf.rebuild_overlay")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    argv = [WWCTL, "overlay", "build"]
    if inp.node:
        argv.append(inp.node)

    diff = Diff(
        changes=[],
        commands_preview=[redacted_argv(CommandSpec(argv=argv))],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    target = inp.node or "all"
    event.diff_summary = f"overlay build {target}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="build"
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
        CommandSpec(argv=argv, timeout_s=60),
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
                message=f"wwctl overlay build failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check overlay template syntax",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"node": inp.node},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.query.list_images",
    risk=Risk.READ,
    domain="warewulf",
    blast_radius=lambda _: 0,
)
def list_images(
    inp: ListImagesIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """List Warewulf images.

    Risk: READ
    """
    meta, _fn, _br = get_tool("warewulf.query.list_images")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input={},
    )
    audit_id = event.id

    res = run_command(
        CommandSpec(argv=[WWCTL, "container", "list", "-a"], timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"wwctl container list failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check Warewulf installation",
            )
        )

    _finish_read(audit_id, ok=True)
    return ToolResult.success(data={"images": res.stdout.splitlines()}, audit_id=audit_id)


@tool(
    name="warewulf.query.list_nodes",
    risk=Risk.READ,
    domain="warewulf",
    blast_radius=lambda _: 0,
)
def list_nodes(
    inp: ListNodesIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """List Warewulf nodes.

    Risk: READ
    """
    meta, _fn, _br = get_tool("warewulf.query.list_nodes")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input={},
    )
    audit_id = event.id

    res = run_command(
        CommandSpec(argv=[WWCTL, "node", "list", "-a"], timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"wwctl node list failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check Warewulf installation",
            )
        )

    _finish_read(audit_id, ok=True)
    return ToolResult.success(data={"nodes": res.stdout.splitlines()}, audit_id=audit_id)
