"""Warewulf provisioning tools. See spec 03.

All commands run via `run_command` under scoped sudo.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
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

# Warewulf overlay files live here on the controller
_WW_OVERLAY_DIR = "/var/lib/warewulf/overlays"

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


class WwNodeStatusIn(BaseModel):
    node: str


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


def _compute_spec_hash(inp: BuildImageIn) -> str:
    spec = {
        "base_image": inp.base_image,
        "kind": inp.kind,
        "packages": sorted(inp.packages),
        "kernel_args": inp.kernel_args,
        "nvidia_driver_version": inp.nvidia_driver_version,
        "cuda_version": inp.cuda_version,
        "enable_fabricmanager": inp.enable_fabricmanager,
        "install_dcgm": inp.install_dcgm,
    }
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


def _cpu_exec_commands(inp: BuildImageIn) -> list[list[str]]:
    base_pkgs = ["kernel", "chrony", "munge", "slurm-slurmd", "nfs-utils", "node_exporter"]
    install_pkgs = base_pkgs + inp.packages
    return [
        [WWCTL, "container", "exec", inp.name, "--", "dnf", "-y", "update"],
        [WWCTL, "container", "exec", inp.name, "--", "dnf", "-y", "install"] + install_pkgs,
        [WWCTL, "container", "exec", inp.name, "--", "systemctl", "enable", "slurmd", "chronyd"],
    ]


def _gpu_exec_commands(inp: BuildImageIn) -> list[list[str]]:
    cmds = _cpu_exec_commands(inp)
    cmds.insert(
        1,
        [WWCTL, "container", "exec", inp.name, "--", "dnf", "-y", "install", "kernel-devel", "kernel-headers"],
    )
    if inp.nvidia_driver_version:
        cmds.append(
            [WWCTL, "container", "exec", inp.name, "--",
             "dnf", "-y", "install", f"nvidia-driver-{inp.nvidia_driver_version}"]
        )
    if inp.cuda_version:
        cmds.append(
            [WWCTL, "container", "exec", inp.name, "--",
             "dnf", "-y", "install", f"cuda-toolkit-{inp.cuda_version}"]
        )
    if inp.enable_fabricmanager and inp.nvidia_driver_version:
        cmds.append(
            [WWCTL, "container", "exec", inp.name, "--",
             "dnf", "-y", "install", f"nvidia-fabricmanager-{inp.nvidia_driver_version}"]
        )
    if inp.install_dcgm:
        cmds.append(
            [WWCTL, "container", "exec", inp.name, "--",
             "dnf", "-y", "install", "datacenter-gpu-manager"]
        )
        cmds.append(
            [WWCTL, "container", "exec", inp.name, "--", "systemctl", "enable", "nvidia-dcgm"]
        )
    return cmds


def _build_exec_commands(inp: BuildImageIn) -> list[list[str]]:
    inner = _gpu_exec_commands(inp) if inp.kind == "compute_gpu" else _cpu_exec_commands(inp)
    inner.append([WWCTL, "container", "build", inp.name])
    return inner


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

    spec_hash = _compute_spec_hash(inp)
    all_cmds = _build_exec_commands(inp)

    diff = Diff(
        changes=[
            {"target": f"image/{inp.name}", "field": "spec_hash", "before": None, "after": spec_hash, "op": "build"}
        ],
        commands_preview=[redacted_argv(CommandSpec(argv=cmd)) for cmd in all_cmds],
        blast_radius=_blast_radius(inp),
        reversible=True,
    )

    event.diff_summary = f"build image {inp.name} kind={inp.kind} spec_hash={spec_hash}"

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

    if g.requires_approval and not g.approved:
        event.decision = "needs_approval"
        event.diff_summary = diff.render()
        event.result_status = "needs_approval"
        audit.commit_event(event)
        return ToolResult.needs_approval(diff)

    for cmd in all_cmds:
        timeout = 900 if cmd[2] == "build" else 300
        res = run_command(CommandSpec(argv=cmd, timeout_s=timeout), actor=actor, audit_id=audit_id)
        if res.rc != 0:
            event.decision = "auto"
            event.diff_summary = diff.render()
            event.result_status = "error"
            audit.commit_event(event)
            step_label = " ".join(cmd[3:6])
            return ToolResult.failed(
                ToolError(
                    kind=ErrorKind.COMMAND_FAILED,
                    message=f"container build step failed [{step_label}] (rc={res.rc})",
                    detail=res.stderr,
                    remediation="check container build logs; verify package repos are reachable",
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

    # Stage files in config repo (git-tracked copy)
    from hpc_agent.state.configrepo import get_config_repo

    repo = get_config_repo()
    for relpath, content in inp.files.items():
        repo.stage(f"warewulf/overlays/{inp.overlay}/{relpath}", content)

    config_commit = repo.commit(
        message=f"overlay {inp.overlay}: update {len(inp.files)} files",
        author=actor,
    )

    # Copy staged files into Warewulf's live overlay directory
    overlay_base = Path(_WW_OVERLAY_DIR) / inp.overlay
    for relpath, content in inp.files.items():
        dest = overlay_base / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)

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
        data={"overlay": inp.overlay, "files": len(inp.files), "config_commit": config_commit},
        diff=diff,
        audit_id=audit_id,
        config_commit=config_commit,
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
    name="warewulf.node_status",
    risk=Risk.READ,
    domain="warewulf",
    blast_radius=lambda _: 0,
)
def ww_node_status(
    inp: WwNodeStatusIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Query a node's provisioning registration in Warewulf.

    Risk: READ
    """
    meta, _fn, _br = get_tool("warewulf.node_status")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input={"node": inp.node},
    )
    audit_id = event.id

    res = run_command(
        CommandSpec(argv=[WWCTL, "node", "show", inp.node], timeout_s=30),
        actor=actor,
        audit_id=audit_id,
    )

    if res.rc != 0:
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.NOT_FOUND,
                message=f"node {inp.node!r} not found in Warewulf (rc={res.rc})",
                detail=res.stderr,
                remediation="run warewulf.provision_node first to register the node",
            )
        )

    # Parse columnar output into key→value pairs
    fields: dict[str, str] = {}
    for line in res.stdout.splitlines():
        if not line.strip() or line.startswith("NODE"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            fields.setdefault("node", parts[0])
            if len(parts) >= 3:
                fields.setdefault("profile", parts[1])
                fields.setdefault("image", parts[2])
            if len(parts) >= 5:
                fields.setdefault("netdev", parts[3])
                fields.setdefault("mac", parts[4])
            if len(parts) >= 6:
                fields.setdefault("ip", parts[5])

    if not fields:
        # No output lines parsed — node not found
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.NOT_FOUND,
                message=f"node {inp.node!r} not registered in Warewulf",
                detail=res.stdout,
                remediation="run warewulf.provision_node first to register the node",
            )
        )

    _finish_read(audit_id, ok=True)
    return ToolResult.success(data={"node": inp.node, **fields}, audit_id=audit_id)


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
