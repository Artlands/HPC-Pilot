"""Slurm QOS management — the reference tool. See spec 05 §1.2.

Implements the full execution contract from spec 00 §3.4:
  validate -> read current -> compute delta -> build Diff -> gate -> dry-run/approval
  -> snapshot -> execute -> commit/state/audit -> ToolResult.

This is the canonical pattern every other mutating tool should follow.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from hpc_agent.config.settings import settings
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandSpec, redacted_argv, run_command
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.diff import Change, Diff
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.state.db import session_scope
from hpc_agent.state.repos import SlurmRepo
from hpc_agent.tools.base import Risk, get_tool, tool
from hpc_agent.tools.errors import ErrorKind, ToolError
from hpc_agent.tools.result import ToolResult
from hpc_agent.tools.slurm_parse import (
    minutes_to_slurm_time,
    parse_pipe_table,
    slurm_time_to_minutes,
)

SACCTMGR = f"{settings.slurm_bin_dir}/sacctmgr"


class ManageQOSIn(BaseModel):
    name: str
    op: Literal["create", "modify"]
    priority: int | None = None
    max_wall_min: int | None = None  # MaxWall
    max_jobs_pu: int | None = None  # MaxJobsPerUser
    max_tres: str | None = None  # MaxTRES, e.g. "cpu=128,gres/gpu=8"
    max_tres_pu: str | None = None  # MaxTRESPerUser
    grp_tres: str | None = None  # GrpTRES
    flags: list[str] = Field(default_factory=list)
    dry_run: bool = True


class ManageUserAssocIn(BaseModel):
    user: str
    account: str
    op: Literal["create", "modify"]
    qos_list: list[str] | None = None
    qos_add: list[str] | None = None
    default_qos: str | None = None
    fairshare: int | None = None
    dry_run: bool = True


# Maps our input fields to sacctmgr "set key=value" tokens and the show-column we read.
_FIELD_MAP: dict[str, tuple[str, str]] = {
    # input_field: (sacctmgr_set_key, show_column)
    "priority": ("Priority", "Priority"),
    "max_jobs_pu": ("MaxJobsPerUser", "MaxJobsPU"),
    "max_tres": ("MaxTRES", "MaxTRES"),
    "max_tres_pu": ("MaxTRESPerUser", "MaxTRESPU"),
    "grp_tres": ("GrpTRES", "GrpTRES"),
}

_ASSOC_FIELD_MAP: dict[str, tuple[str, str]] = {
    "default_qos": ("DefaultQOS", "DefaultQOS"),
    "fairshare": ("Fairshare", "FairShare"),
}


def _read_current(name: str, *, actor: str, audit_id: str) -> dict[str, str] | None:
    spec = CommandSpec(
        argv=[SACCTMGR, "show", "qos", name, "-P", "--noheader=no"],
        timeout_s=30,
    )
    res = run_command(spec, actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    rows = parse_pipe_table(res.stdout)
    return rows[0] if rows else None


def _read_assoc(inp: ManageUserAssocIn, *, actor: str, audit_id: str) -> dict[str, str] | None:
    spec = CommandSpec(
        argv=[
            SACCTMGR,
            "show",
            "assoc",
            f"user={inp.user}",
            f"account={inp.account}",
            "-P",
            "--noheader=no",
        ],
        timeout_s=30,
    )
    res = run_command(spec, actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    rows = parse_pipe_table(res.stdout)
    return rows[0] if rows else None


def _qos_exists(name: str, *, actor: str, audit_id: str) -> bool:
    return _read_current(name, actor=actor, audit_id=audit_id) is not None


def _desired_changes(inp: ManageQOSIn, current: dict[str, str] | None) -> list[Change]:
    changes: list[Change] = []
    target = f"qos/{inp.name}"

    if inp.op == "create" and current is None:
        changes.append(Change(target=target, op="create", after=inp.name))

    # MaxWall (time format conversion handled specially)
    if inp.max_wall_min is not None:
        before = current.get("MaxWall") if current else None
        before_min = slurm_time_to_minutes(before)
        if before_min != inp.max_wall_min:
            changes.append(
                Change(
                    target=target,
                    field="max_wall_min",
                    op="modify",
                    before=str(before_min) if before_min is not None else None,
                    after=str(inp.max_wall_min),
                )
            )

    for field_name, (_set_key, show_col) in _FIELD_MAP.items():
        new_val = getattr(inp, field_name)
        if new_val is None:
            continue
        before = current.get(show_col) if current else None
        if str(before) != str(new_val):
            changes.append(
                Change(
                    target=target,
                    field=field_name,
                    op="modify",
                    before=before,
                    after=str(new_val),
                )
            )
    return changes


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def _join_csv(values: list[str]) -> str:
    return ",".join(values)


def _current_qos_list(current: dict[str, str] | None) -> list[str]:
    if current is None:
        return []
    return _split_csv(current.get("QOS") or current.get("QOSRAW"))


def _assoc_requested_qos(inp: ManageUserAssocIn) -> list[str]:
    names: list[str] = []
    for values in (inp.qos_list, inp.qos_add, [inp.default_qos] if inp.default_qos else None):
        if values:
            for name in values:
                if name not in names:
                    names.append(name)
    return names


def _assoc_target_qos(inp: ManageUserAssocIn, current: dict[str, str] | None) -> str | None:
    if inp.qos_list is not None:
        return _join_csv(inp.qos_list)
    if inp.qos_add is not None:
        merged = _current_qos_list(current)
        for qos in inp.qos_add:
            if qos not in merged:
                merged.append(qos)
        return _join_csv(merged)
    return None


def _assoc_desired_changes(
    inp: ManageUserAssocIn, current: dict[str, str] | None
) -> list[Change]:
    changes: list[Change] = []
    target = f"assoc/{inp.user}@{inp.account}"

    if inp.op == "create" and current is None:
        changes.append(Change(target=target, op="create", after=f"{inp.user}@{inp.account}"))

    target_qos = _assoc_target_qos(inp, current)
    if target_qos is not None:
        before_qos = _join_csv(_current_qos_list(current))
        if before_qos != target_qos:
            changes.append(
                Change(
                    target=target,
                    field="qos_list" if inp.qos_list is not None else "qos_add",
                    op="modify",
                    before=before_qos or None,
                    after=target_qos,
                )
            )

    for field_name, (_set_key, show_col) in _ASSOC_FIELD_MAP.items():
        new_val = getattr(inp, field_name)
        if new_val is None:
            continue
        before = current.get(show_col) if current else None
        if str(before) != str(new_val):
            changes.append(
                Change(
                    target=target,
                    field=field_name,
                    op="modify",
                    before=before,
                    after=str(new_val),
                )
            )
    return changes


def _build_set_tokens(inp: ManageQOSIn) -> list[str]:
    tokens: list[str] = []
    if inp.max_wall_min is not None:
        tokens.append(f"MaxWall={minutes_to_slurm_time(inp.max_wall_min)}")
    for field_name, (set_key, _col) in _FIELD_MAP.items():
        val = getattr(inp, field_name)
        if val is not None:
            tokens.append(f"{set_key}={val}")
    if inp.flags:
        tokens.append(f"Flags={','.join(inp.flags)}")
    return tokens


def _build_assoc_create_tokens(inp: ManageUserAssocIn) -> list[str]:
    tokens = [f"account={inp.account}"]
    if inp.default_qos is not None:
        tokens.append(f"DefaultQOS={inp.default_qos}")
    if inp.fairshare is not None:
        tokens.append(f"Fairshare={inp.fairshare}")
    return tokens


def _build_assoc_modify_tokens(inp: ManageUserAssocIn) -> list[str]:
    tokens: list[str] = []
    if inp.qos_list is not None:
        tokens.append(f"QOS={_join_csv(inp.qos_list)}")
    if inp.qos_add is not None:
        tokens.extend(f"QOS+={qos}" for qos in inp.qos_add)
    if inp.default_qos is not None:
        tokens.append(f"DefaultQOS={inp.default_qos}")
    if inp.fairshare is not None:
        tokens.append(f"Fairshare={inp.fairshare}")
    return tokens


def _forward_argv(inp: ManageQOSIn) -> list[str]:
    set_tokens = _build_set_tokens(inp)
    if inp.op == "create":
        return [SACCTMGR, "-i", "add", "qos", inp.name, "set", *set_tokens]
    return [SACCTMGR, "-i", "modify", "qos", inp.name, "set", *set_tokens]


def _assoc_forward_argvs(inp: ManageUserAssocIn) -> list[list[str]]:
    if inp.op == "create":
        commands = [[SACCTMGR, "-i", "add", "user", inp.user, *_build_assoc_create_tokens(inp)]]
        qos_tokens = _build_assoc_modify_tokens(
            ManageUserAssocIn(
                user=inp.user,
                account=inp.account,
                op="modify",
                qos_list=inp.qos_list,
                qos_add=inp.qos_add,
            )
        )
        if qos_tokens:
            commands.append(
                [
                    SACCTMGR,
                    "-i",
                    "modify",
                    "user",
                    inp.user,
                    f"account={inp.account}",
                    "set",
                    *qos_tokens,
                ]
            )
        return commands
    return [
        [
            SACCTMGR,
            "-i",
            "modify",
            "user",
            inp.user,
            f"account={inp.account}",
            "set",
            *_build_assoc_modify_tokens(inp),
        ]
    ]


def _inverse_argv(inp: ManageQOSIn, current: dict[str, str] | None) -> list[list[str]]:
    """Build the inverse command(s) for revert (spec 01 §5)."""
    if inp.op == "create":
        # Inverse of create is delete — prohibited to auto-run; record for the human.
        return [[SACCTMGR, "-i", "delete", "qos", inp.name]]
    if current is None:
        return []
    restore: list[str] = []
    if inp.max_wall_min is not None:
        prior = current.get("MaxWall") or "-1"
        restore.append(f"MaxWall={prior}")
    for field_name, (set_key, col) in _FIELD_MAP.items():
        if getattr(inp, field_name) is not None:
            prior = current.get(col) or "-1"
            restore.append(f"{set_key}={prior}")
    if not restore:
        return []
    return [[SACCTMGR, "-i", "modify", "qos", inp.name, "set", *restore]]


def _assoc_inverse_argv(inp: ManageUserAssocIn, current: dict[str, str] | None) -> list[list[str]]:
    if inp.op == "create":
        return [[SACCTMGR, "-i", "delete", "user", inp.user, f"account={inp.account}"]]
    if current is None:
        return []
    restore: list[str] = []
    if inp.qos_list is not None or inp.qos_add is not None:
        restore.append(f"QOS={_join_csv(_current_qos_list(current))}")
    if inp.default_qos is not None:
        restore.append(f"DefaultQOS={current.get('DefaultQOS') or '-1'}")
    if inp.fairshare is not None:
        restore.append(f"Fairshare={current.get('FairShare') or '-1'}")
    if not restore:
        return []
    return [
        [
            SACCTMGR,
            "-i",
            "modify",
            "user",
            inp.user,
            f"account={inp.account}",
            "set",
            *restore,
        ]
    ]


def _blast_radius(inp: BaseModel) -> int:
    return 1  # a single QOS


def _assoc_blast_radius(inp: BaseModel) -> int:
    return 1  # a single user/account association


def _persist_qos(inp: ManageQOSIn) -> None:
    """Upsert the desired-state QOS row. Tolerant: if no DB is configured (e.g. some unit
    tests), the connection error is swallowed — audit remains the revert source of truth."""
    fields: dict[str, object] = {}
    if inp.priority is not None:
        fields["priority"] = inp.priority
    if inp.max_wall_min is not None:
        fields["max_wall_min"] = inp.max_wall_min
    if inp.max_jobs_pu is not None:
        fields["max_jobs_pu"] = inp.max_jobs_pu
    if inp.max_tres is not None:
        fields["max_tres"] = inp.max_tres
    if inp.grp_tres is not None:
        fields["grp_tres"] = inp.grp_tres
    try:
        with session_scope() as s:
            SlurmRepo(s).upsert_qos(inp.name, **fields)
    except Exception:  # noqa: BLE001 - state persistence is best-effort; audit is canonical
        pass


def _persist_assoc(inp: ManageUserAssocIn, current: dict[str, str] | None) -> None:
    fields: dict[str, object] = {}
    qos_list = _assoc_target_qos(inp, current)
    if qos_list is not None:
        fields["qos_list"] = qos_list
    elif current is not None:
        fields["qos_list"] = _join_csv(_current_qos_list(current))
    if inp.default_qos is not None:
        fields["default_qos"] = inp.default_qos
    if inp.fairshare is not None:
        fields["fairshare"] = inp.fairshare
    try:
        with session_scope() as s:
            SlurmRepo(s).upsert_assoc(inp.user, inp.account, **fields)
    except Exception:  # noqa: BLE001 - state persistence is best-effort; audit is canonical
        pass


@tool(name="slurm.manage_qos", risk=Risk.MEDIUM, domain="slurm", blast_radius=_blast_radius)
def manage_qos(
    inp: ManageQOSIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
    persist_state: bool = True,
) -> ToolResult:
    """Create or modify a Slurm QOS (priority, limits, TRES). Reference implementation."""
    meta, _fn, _br = get_tool("slurm.manage_qos")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        # 1. semantic precondition checks
        if inp.op == "modify" and not _build_set_tokens(inp):
            raise _precondition("modify requires at least one field to change")

        # 2. read current state
        current = _read_current(inp.name, actor=actor, audit_id=audit_id)
        if inp.op == "modify" and current is None:
            raise _err(ErrorKind.NOT_FOUND, f"qos '{inp.name}' does not exist", "create it first")
        if inp.op == "create" and current is not None:
            # Treat as modify-to-converge (idempotent create).
            pass

        # 3. compute delta
        changes = _desired_changes(inp, current)

        # 4. build Diff
        forward = _forward_argv(inp)
        diff = Diff(
            changes=changes,
            commands_preview=[redacted_argv(CommandSpec(argv=forward))],
            blast_radius=_blast_radius(inp),
            reversible=(inp.op == "modify"),
            revert_hint=None if inp.op == "modify" else "create is reverted only by manual delete",
        )
        if diff.is_noop():
            event.decision = "auto"
            event.diff_summary = "no-op"
            event.result_status = "ok"
            audit.commit_event(event)
            return ToolResult.success(data={"qos": inp.name, "noop": True}, audit_id=audit_id)

        # 5. gate
        g = gate_override or safety_gate.evaluate(
            meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op=inp.op
        )
        if g.denied:
            event.decision = "denied"
            event.diff_summary = diff.render()
            event.result_status = "denied"
            audit.commit_event(event)
            return ToolResult.denied(g.reason or "denied by policy")

        # 6. dry-run
        if inp.dry_run:
            event.decision = "dry_run"
            event.diff_summary = diff.render()
            event.result_status = "dry_run"
            audit.commit_event(event)
            return ToolResult.dry_run(diff)

        # 7. approval
        if g.requires_approval and not g.approved:
            event.decision = "needs_approval"
            event.diff_summary = diff.render()
            event.result_status = "needs_approval"
            audit.commit_event(event)
            return ToolResult.needs_approval(diff)

        # 8. (QOS lives in slurmdbd, not slurm.conf — no config snapshot needed)
        # 9. execute
        res = run_command(CommandSpec(argv=forward, timeout_s=60), actor=actor, audit_id=audit_id)
        if res.rc != 0:
            event.decision = _decision_str(g)
            event.diff_summary = diff.render()
            event.result_status = "error"
            audit.commit_event(event)
            return ToolResult.failed(
                ToolError(
                    kind=ErrorKind.COMMAND_FAILED,
                    message=f"sacctmgr failed (rc={res.rc})",
                    detail=res.stderr,
                    remediation="check qos name and TRES syntax",
                )
            )

        # 10. record inverse + commit audit + state upsert via SlurmRepo
        event.decision = _decision_str(g)
        event.diff_summary = diff.render()
        event.result_status = "ok"
        event.revert_argv = _inverse_argv(inp, current)
        audit.commit_event(event)

        # State-store upsert: desired-state row reflecting what we just applied.
        if persist_state:
            _persist_qos(inp)

        return ToolResult.success(
            data={"qos": inp.name, "applied": [c.field for c in changes if c.field]},
            diff=diff,
            audit_id=audit_id,
        )

    except _ToolBoundaryError as exc:
        event.result_status = "error"
        event.decision = "error"
        audit.commit_event(event)
        return ToolResult.failed(exc.error)


@tool(
    name="slurm.manage_user_assoc",
    risk=Risk.MEDIUM,
    domain="slurm",
    blast_radius=_assoc_blast_radius,
)
def manage_user_assoc(
    inp: ManageUserAssocIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
    persist_state: bool = True,
) -> ToolResult:
    """Create or modify a Slurm user/account association and QOS access."""
    meta, _fn, _br = get_tool("slurm.manage_user_assoc")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        if inp.op == "modify" and not _build_assoc_modify_tokens(inp):
            raise _precondition("modify requires at least one field to change")
        if inp.qos_list is not None and inp.qos_add is not None:
            raise _precondition("use either qos_list or qos_add, not both")

        for qos in _assoc_requested_qos(inp):
            if not _qos_exists(qos, actor=actor, audit_id=audit_id):
                raise _precondition(f"qos '{qos}' does not exist; create it with manage_qos first")

        current = _read_assoc(inp, actor=actor, audit_id=audit_id)
        if inp.op == "modify" and current is None:
            raise _err(
                ErrorKind.NOT_FOUND,
                f"association '{inp.user}@{inp.account}' does not exist",
                "create it first",
            )

        changes = _assoc_desired_changes(inp, current)
        forward = _assoc_forward_argvs(inp)
        diff = Diff(
            changes=changes,
            commands_preview=[redacted_argv(CommandSpec(argv=argv)) for argv in forward],
            blast_radius=_assoc_blast_radius(inp),
            reversible=(inp.op == "modify"),
            revert_hint=None if inp.op == "modify" else "create is reverted only by manual delete",
        )
        if diff.is_noop():
            event.decision = "auto"
            event.diff_summary = "no-op"
            event.result_status = "ok"
            audit.commit_event(event)
            return ToolResult.success(data={"user": inp.user, "account": inp.account, "noop": True})

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

        for argv in forward:
            res = run_command(CommandSpec(argv=argv, timeout_s=60), actor=actor, audit_id=audit_id)
            if res.rc != 0:
                event.decision = _decision_str(g)
                event.diff_summary = diff.render()
                event.result_status = "error"
                audit.commit_event(event)
                return ToolResult.failed(
                    ToolError(
                        kind=ErrorKind.COMMAND_FAILED,
                        message=f"sacctmgr failed (rc={res.rc})",
                        detail=res.stderr,
                        remediation="check user, account, and QOS names",
                    )
                )

        event.decision = _decision_str(g)
        event.diff_summary = diff.render()
        event.result_status = "ok"
        event.revert_argv = _assoc_inverse_argv(inp, current)
        audit.commit_event(event)

        if persist_state:
            _persist_assoc(inp, current)

        return ToolResult.success(
            data={"user": inp.user, "account": inp.account, "applied": True},
            diff=diff,
            audit_id=audit_id,
        )

    except _ToolBoundaryError as exc:
        event.result_status = "error"
        event.decision = "error"
        audit.commit_event(event)
        return ToolResult.failed(exc.error)


# --- small internal error helpers (kept local to the reference tool) ---


class _ToolBoundaryError(Exception):
    def __init__(self, error: ToolError) -> None:
        super().__init__(error.message)
        self.error = error


def _err(kind: ErrorKind, message: str, remediation: str | None = None) -> _ToolBoundaryError:
    return _ToolBoundaryError(ToolError(kind=kind, message=message, remediation=remediation))


def _precondition(message: str) -> _ToolBoundaryError:
    return _err(ErrorKind.PRECONDITION, message)


def _decision_str(g: safety_gate.Gate) -> str:
    if g.approved:
        return f"approved-by:{g.approver or 'unknown'}"
    return "auto"
