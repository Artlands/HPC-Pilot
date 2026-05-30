"""Warewulf controller bootstrap tools. See spec 09.

Day-0 setup: configure DHCP, TFTP, and NFS so compute nodes can PXE-boot.

Warewulf 4.x is driven by ``/etc/warewulf/warewulf.conf`` (YAML). These tools edit the
managed copy of that file in the config repo (config-as-code, spec 00 §2), commit it,
then run ``wwctl configure <service>`` — which reads warewulf.conf and (re)writes the
live dhcpd/TFTP/NFS configuration. The ``wwctl configure`` subcommands take no
positional options; all parameters flow through warewulf.conf. All commands run via
`run_command` under scoped sudo.
"""

from __future__ import annotations

import copy
import difflib
import shutil
from collections.abc import Callable
from typing import Any

import yaml
from pydantic import BaseModel

from hpc_agent.config.settings import settings
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandSpec, redacted_argv, run_command
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.diff import Change, Diff
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.base import Risk, get_tool, tool
from hpc_agent.tools.errors import ErrorKind, ToolError
from hpc_agent.tools.result import ToolResult

WWCTL = f"{settings.ww_bin_dir}/wwctl"

# Managed copy of /etc/warewulf/warewulf.conf inside the config repo.
_WAREWULF_CONF = "warewulf/warewulf.conf"

# warewulf.conf is a nested YAML document; values are heterogeneous.
Conf = dict[str, Any]


class ServerStatusIn(BaseModel):
    pass


class ConfigureDhcpIn(BaseModel):
    interface: str  # provisioning NIC, documented in the audit/diff
    range_start: str  # first PXE lease, e.g. "192.168.122.100"
    range_end: str  # last PXE lease, e.g. "192.168.122.200"
    controller_ip: str | None = None  # warewulf.conf ipaddr (provisioning IP)
    netmask: str = "255.255.255.0"
    dry_run: bool = True


class ConfigureTftpIn(BaseModel):
    enabled: bool = True
    dry_run: bool = True


class ConfigureNfsIn(BaseModel):
    exports: list[str] = ["/home", "/scratch", "/opt/spack"]
    export_options: str = "rw,sync,no_root_squash"
    dry_run: bool = True


def _finish_read(audit_id: str, *, ok: bool) -> None:
    event = audit.get_event(audit_id)
    if event is None:
        return
    event.decision = "auto"
    event.result_status = "ok" if ok else "error"
    audit.commit_event(event)


# --- warewulf.conf helpers (config-as-code) -------------------------------------------


def _load_conf(repo: object) -> Conf:
    try:
        raw = repo.read(_WAREWULF_CONF)  # type: ignore[attr-defined]
    except FileNotFoundError:
        return {}
    return yaml.safe_load(raw) or {}


def _dump_conf(conf: Conf) -> str:
    return yaml.safe_dump(conf, sort_keys=True, default_flow_style=False)


def _conf_diff(before: Conf, after: Conf) -> str:
    before_lines = _dump_conf(before).splitlines(keepends=True)
    after_lines = _dump_conf(after).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines, after_lines, fromfile="a/warewulf.conf", tofile="b/warewulf.conf"
        )
    )


def _plan_conf(mutate: Callable[[Conf], None]) -> tuple[Conf, Conf, bool, str | None]:
    """Read warewulf.conf, apply `mutate` to a copy, return (before, after, changed, diff)."""
    from hpc_agent.state.configrepo import get_config_repo

    repo = get_config_repo()
    before = _load_conf(repo)
    after = copy.deepcopy(before)
    mutate(after)
    changed = after != before
    diff_text = _conf_diff(before, after) if changed else None
    return before, after, changed, diff_text


def _persist_conf(after: Conf, *, message: str, actor: str) -> str:
    from hpc_agent.state.configrepo import get_config_repo

    repo = get_config_repo()
    repo.stage(_WAREWULF_CONF, _dump_conf(after))
    return repo.commit(message=message, author=actor)


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
    Returns PRECONDITION error if the wwctl binary is absent. Otherwise always succeeds;
    service-state booleans are best-effort parsed from `wwctl server status` text and the
    caller (bootstrap workflow) decides what to do with them.
    """
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
                message="wwctl binary not found; install warewulf before bootstrapping",
                detail=None,
                remediation="install the warewulf (or warewulf-ohpc) package on the controller",
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

    # Parse version from a line like "Warewulf v4.5.2"
    version: str | None = None
    for line in res.stdout.splitlines():
        if "warewulf" in line.lower():
            for token in line.split():
                if token.startswith("v") and token[1:2].isdigit():
                    version = token
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
    blast_radius=lambda _: 1,  # one service; HIGH risk forces approval regardless
)
def configure_dhcp(
    inp: ConfigureDhcpIn,
    *,
    actor: str,
    actor_role: Role = Role.ADMIN,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Configure Warewulf's built-in DHCP server via warewulf.conf.

    Risk: HIGH — controls network boot for every node. Always requires approval.
    Idempotent: re-running with unchanged inputs yields an empty config diff and skips
    the `wwctl configure dhcp` call.
    """
    meta, _fn, _br = get_tool("warewulf.configure_dhcp")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    def mutate(conf: Conf) -> None:
        if inp.controller_ip:
            conf["ipaddr"] = inp.controller_ip
            conf["netmask"] = inp.netmask
        dhcp = conf.setdefault("dhcp", {})
        dhcp["enabled"] = True
        dhcp["range start"] = inp.range_start
        dhcp["range end"] = inp.range_end

    before, after, changed, config_diff = _plan_conf(mutate)

    diff = Diff(
        changes=(
            [
                Change(
                    target="warewulf.conf/dhcp",
                    field="range",
                    before=None,
                    after=f"{inp.range_start}-{inp.range_end} (nic {inp.interface})",
                    op="configure",
                )
            ]
            if changed
            else []
        ),
        config_diff=config_diff,
        commands_preview=[redacted_argv(CommandSpec(argv=[WWCTL, "configure", "dhcp"]))]
        if changed
        else [],
        blast_radius=1,
        reversible=True,
        revert_hint="restore the prior warewulf.conf commit and re-run `wwctl configure dhcp`",
    )

    event.diff_summary = f"configure dhcp {inp.range_start}-{inp.range_end} nic={inp.interface}"

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

    if not changed:
        event.decision = "auto"
        event.diff_summary = "(no changes — dhcp already configured)"
        event.result_status = "ok"
        audit.commit_event(event)
        return ToolResult.success(data={"changed": False}, diff=diff, audit_id=audit_id)

    config_commit = _persist_conf(
        after, message=f"warewulf dhcp range {inp.range_start}-{inp.range_end}", actor=actor
    )

    res = run_command(
        CommandSpec(argv=[WWCTL, "configure", "dhcp"], timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "approved-by:operator"
        event.diff_summary = diff.render()
        event.result_status = "error"
        event.config_commit = config_commit
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"wwctl configure dhcp failed (rc={res.rc})",
                detail=res.stderr,
                remediation="verify warewulf.conf dhcp section and that dhcpd is installed",
            )
        )

    event.decision = "approved-by:operator"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    event.config_commit = config_commit
    audit.commit_event(event)
    return ToolResult.success(
        data={
            "changed": True,
            "range_start": inp.range_start,
            "range_end": inp.range_end,
            "interface": inp.interface,
        },
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
    """Enable the TFTP server (PXE/iPXE delivery) via warewulf.conf.

    Risk: MEDIUM. Idempotent on the warewulf.conf tftp section.
    """
    meta, _fn, _br = get_tool("warewulf.configure_tftp")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    def mutate(conf: Conf) -> None:
        conf.setdefault("tftp", {})["enabled"] = inp.enabled

    before, after, changed, config_diff = _plan_conf(mutate)

    diff = Diff(
        changes=(
            [
                Change(
                    target="warewulf.conf/tftp",
                    field="enabled",
                    before=None,
                    after=str(inp.enabled),
                    op="configure",
                )
            ]
            if changed
            else []
        ),
        config_diff=config_diff,
        commands_preview=[redacted_argv(CommandSpec(argv=[WWCTL, "configure", "tftp"]))]
        if changed
        else [],
        blast_radius=1,
        reversible=True,
        revert_hint="restore the prior warewulf.conf commit and re-run `wwctl configure tftp`",
    )

    event.diff_summary = f"configure tftp enabled={inp.enabled}"

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

    if not changed:
        event.decision = "auto"
        event.diff_summary = "(no changes — tftp already configured)"
        event.result_status = "ok"
        audit.commit_event(event)
        return ToolResult.success(data={"changed": False}, diff=diff, audit_id=audit_id)

    config_commit = _persist_conf(after, message="warewulf tftp enabled", actor=actor)

    res = run_command(
        CommandSpec(argv=[WWCTL, "configure", "tftp"], timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        event.config_commit = config_commit
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"wwctl configure tftp failed (rc={res.rc})",
                detail=res.stderr,
                remediation="ensure the tftp-server package is installed",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    event.config_commit = config_commit
    audit.commit_event(event)
    return ToolResult.success(
        data={"changed": True, "enabled": inp.enabled},
        diff=diff,
        audit_id=audit_id,
        config_commit=config_commit,
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
    """Configure NFS exports (home/scratch/spack) via warewulf.conf.

    Risk: MEDIUM. Idempotent on the warewulf.conf nfs section. Client-network
    restriction is enforced separately by the `firewalld` role (spec 04), not here.
    """
    meta, _fn, _br = get_tool("warewulf.configure_nfs")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    def mutate(conf: Conf) -> None:
        nfs = conf.setdefault("nfs", {})
        nfs["enabled"] = True
        nfs["exports"] = [
            {"path": path, "export options": inp.export_options} for path in inp.exports
        ]

    before, after, changed, config_diff = _plan_conf(mutate)

    diff = Diff(
        changes=(
            [
                Change(
                    target="warewulf.conf/nfs",
                    field="exports",
                    before=None,
                    after=", ".join(inp.exports),
                    op="configure",
                )
            ]
            if changed
            else []
        ),
        config_diff=config_diff,
        commands_preview=[redacted_argv(CommandSpec(argv=[WWCTL, "configure", "nfs"]))]
        if changed
        else [],
        blast_radius=len(inp.exports),
        reversible=True,
        revert_hint="restore the prior warewulf.conf commit and re-run `wwctl configure nfs`",
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

    if not changed:
        event.decision = "auto"
        event.diff_summary = "(no changes — nfs already configured)"
        event.result_status = "ok"
        audit.commit_event(event)
        return ToolResult.success(data={"changed": False}, diff=diff, audit_id=audit_id)

    config_commit = _persist_conf(
        after, message=f"warewulf nfs exports: {len(inp.exports)}", actor=actor
    )

    res = run_command(
        CommandSpec(argv=[WWCTL, "configure", "nfs"], timeout_s=60),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "error"
        event.config_commit = config_commit
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"wwctl configure nfs failed (rc={res.rc})",
                detail=res.stderr,
                remediation="ensure nfs-utils is installed and the export paths exist",
            )
        )

    event.decision = "auto"
    event.diff_summary = diff.render()
    event.result_status = "ok"
    event.config_commit = config_commit
    audit.commit_event(event)
    return ToolResult.success(
        data={"changed": True, "exports": inp.exports},
        diff=diff,
        audit_id=audit_id,
        config_commit=config_commit,
    )
