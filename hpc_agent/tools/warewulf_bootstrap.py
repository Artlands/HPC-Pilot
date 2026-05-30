"""Warewulf controller bootstrap tools. See spec 09.

Day-0 setup: configure DHCP, TFTP, and NFS so compute nodes can PXE-boot.
All commands run via `run_command` under scoped sudo.
"""

from __future__ import annotations

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


class ServerStatusIn(BaseModel):
    pass


class ConfigureDhcpIn(BaseModel):
    interface: str
    range_start: str
    range_end: str
    router: str | None = None
    dry_run: bool = True


class ConfigureTftpIn(BaseModel):
    interface: str | None = None
    dry_run: bool = True


class ConfigureNfsIn(BaseModel):
    exports: list[str] = ["/home", "/scratch", "/opt/spack"]
    network: str | None = None
    dry_run: bool = True


def _finish_read(audit_id: str, *, ok: bool) -> None:
    event = audit.get_event(audit_id)
    if event is None:
        return
    event.decision = "auto"
    event.result_status = "ok" if ok else "error"
    audit.commit_event(event)


@tool(
    name="warewulf.server_status",
    risk=Risk.READ,
    domain="warewulf",
    blast_radius=lambda _: 0,
)
def server_status(
    inp: ServerStatusIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Check Warewulf server installation and service status.

    Risk: READ
    Returns PRECONDITION error if wwctl binary is absent.
    """
    import shutil

    meta, _fn, _br = get_tool("warewulf.server_status")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input={},
    )
    audit_id = event.id

    if not shutil.which("wwctl"):
        _finish_read(audit_id, ok=False)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.PRECONDITION,
                message="wwctl binary not found; install warewulf-ohpc before bootstrapping",
                detail=None,
                remediation="install the warewulf-ohpc package on the controller node",
            )
        )

    res = run_command(
        CommandSpec(argv=[WWCTL, "server", "status"], timeout_s=30),
        actor=actor,
        audit_id=audit_id,
    )

    out = res.stdout.lower()
    running = "running" in out or res.rc == 0
    dhcp_ok = "dhcp" in out and "configured" in out
    tftp_ok = "tftp" in out and "configured" in out
    nfs_ok = "nfs" in out and "configured" in out

    # Parse version from first line: "Warewulf v4.5.2"
    version: str | None = None
    for line in res.stdout.splitlines():
        if "warewulf" in line.lower() and "v" in line:
            parts = line.split()
            for p in parts:
                if p.startswith("v") and p[1:2].isdigit():
                    version = p
                    break
            if version:
                break

    _finish_read(audit_id, ok=True)
    return ToolResult.success(
        data={
            "installed": True,
            "running": running,
            "version": version,
            "dhcp_configured": dhcp_ok,
            "tftp_configured": tftp_ok,
            "nfs_configured": nfs_ok,
        },
        audit_id=audit_id,
    )


@tool(
    name="warewulf.configure_dhcp",
    risk=Risk.HIGH,
    domain="warewulf",
    blast_radius=lambda _: 100,  # affects all nodes on the management network
)
def configure_dhcp(
    inp: ConfigureDhcpIn,
    *,
    actor: str,
    actor_role: Role = Role.ADMIN,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Configure Warewulf's built-in DHCP server for the PXE management network.

    Risk: HIGH — affects network boot for every node.
    Always requires explicit approval.
    """
    meta, _fn, _br = get_tool("warewulf.configure_dhcp")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    argv = [
        WWCTL, "configure", "dhcp",
        "--interface", inp.interface,
        "--range-start", inp.range_start,
        "--range-end", inp.range_end,
    ]
    if inp.router:
        argv += ["--router", inp.router]

    diff = Diff(
        changes=[
            {
                "target": "warewulf/dhcp",
                "field": "range",
                "before": None,
                "after": f"{inp.range_start}–{inp.range_end} on {inp.interface}",
                "op": "configure",
            }
        ],
        commands_preview=[redacted_argv(CommandSpec(argv=argv))],
        blast_radius=100,
        reversible=False,
    )

    event.diff_summary = f"configure dhcp {inp.interface} {inp.range_start}-{inp.range_end}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="configure"
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

    res = run_command(CommandSpec(argv=argv, timeout_s=60), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"wwctl configure dhcp failed (rc={res.rc})",
                detail=res.stderr,
                remediation="check interface name and IP range are valid",
            )
        )

    # Record config in the config repo
    from hpc_agent.state.configrepo import get_config_repo

    repo = get_config_repo()
    dhcp_record = (
        f"interface: {inp.interface}\n"
        f"range_start: {inp.range_start}\n"
        f"range_end: {inp.range_end}\n"
        f"router: {inp.router or ''}\n"
    )
    repo.stage("warewulf/dhcp.yaml", dhcp_record)
    config_commit = repo.commit(message=f"warewulf dhcp: {inp.range_start}-{inp.range_end}", author=actor)

    event.decision = "approved-by:operator"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"interface": inp.interface, "range_start": inp.range_start, "range_end": inp.range_end},
        diff=diff,
        audit_id=audit_id,
        config_commit=config_commit,
    )


@tool(
    name="warewulf.configure_tftp",
    risk=Risk.MEDIUM,
    domain="warewulf",
    blast_radius=lambda _: 1,
)
def configure_tftp(
    inp: ConfigureTftpIn,
    *,
    actor: str,
    actor_role: Role = Role.ADMIN,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Configure and enable the TFTP server for PXE boot.

    Risk: MEDIUM
    """
    meta, _fn, _br = get_tool("warewulf.configure_tftp")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    argv = [WWCTL, "configure", "tftp"]
    if inp.interface:
        argv += ["--interface", inp.interface]

    diff = Diff(
        changes=[
            {
                "target": "warewulf/tftp",
                "field": "interface",
                "before": None,
                "after": inp.interface or "all",
                "op": "configure",
            }
        ],
        commands_preview=[redacted_argv(CommandSpec(argv=argv))],
        blast_radius=1,
        reversible=True,
    )

    event.diff_summary = f"configure tftp interface={inp.interface or 'all'}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="configure"
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

    res = run_command(CommandSpec(argv=argv, timeout_s=60), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"wwctl configure tftp failed (rc={res.rc})",
                detail=res.stderr,
                remediation="ensure tftp-server package is installed",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"interface": inp.interface},
        diff=diff,
        audit_id=audit_id,
    )


@tool(
    name="warewulf.configure_nfs",
    risk=Risk.MEDIUM,
    domain="warewulf",
    blast_radius=lambda inp: len(inp.exports) if hasattr(inp, "exports") else 1,
)
def configure_nfs(
    inp: ConfigureNfsIn,
    *,
    actor: str,
    actor_role: Role = Role.ADMIN,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Configure NFS exports for compute node home/scratch/spack mounts.

    Risk: MEDIUM
    """
    meta, _fn, _br = get_tool("warewulf.configure_nfs")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    export_argvs = []
    for export_path in inp.exports:
        argv = [WWCTL, "configure", "nfs", "--export", export_path]
        if inp.network:
            argv += ["--cidr", inp.network]
        export_argvs.append(argv)

    diff = Diff(
        changes=[
            {
                "target": "warewulf/nfs",
                "field": "exports",
                "before": None,
                "after": ", ".join(inp.exports),
                "op": "configure",
            }
        ],
        commands_preview=[redacted_argv(CommandSpec(argv=argv)) for argv in export_argvs],
        blast_radius=len(inp.exports),
        reversible=True,
    )

    event.diff_summary = f"configure nfs exports: {', '.join(inp.exports)}"

    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="configure"
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

    for argv in export_argvs:
        res = run_command(CommandSpec(argv=argv, timeout_s=60), actor=actor, audit_id=audit_id)
        if res.rc != 0:
            event.decision = "auto"
            event.diff_summary = diff.render()
            event.result_status = "error"
            audit.commit_event(event)
            return ToolResult.failed(
                ToolError(
                    kind=ErrorKind.COMMAND_FAILED,
                    message=f"wwctl configure nfs failed for {argv[-1]} (rc={res.rc})",
                    detail=res.stderr,
                    remediation="ensure nfs-utils is installed and export path exists",
                )
            )

    # Stage NFS export record in config repo
    from hpc_agent.state.configrepo import get_config_repo

    repo = get_config_repo()
    nfs_content = "\n".join(
        f"{path} {inp.network or '*'}(rw,no_root_squash)" for path in inp.exports
    )
    repo.stage("warewulf/nfs_exports.conf", nfs_content)
    config_commit = repo.commit(
        message=f"warewulf nfs: {len(inp.exports)} exports", author=actor
    )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"exports": inp.exports, "network": inp.network},
        diff=diff,
        audit_id=audit_id,
        config_commit=config_commit,
    )
