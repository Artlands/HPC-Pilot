"""Slurm QOS management — the reference tool. See spec 05 §1.2.

Implements the full execution contract from spec 00 §3.4:
  validate -> read current -> compute delta -> build Diff -> gate -> dry-run/approval
  -> snapshot -> execute -> commit/state/audit -> ToolResult.

This is the canonical pattern every other mutating tool should follow.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from hpc_agent.config.settings import settings
from hpc_agent.exec import audit
from hpc_agent.exec.rbac import Role
from hpc_agent.exec.runner import CommandSpec, redacted_argv, run_command
from hpc_agent.safety import gate as safety_gate
from hpc_agent.safety.diff import Change, Diff
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.state.db import session_scope
from hpc_agent.state.models import NodeState, Partition
from hpc_agent.state.repos import NodeRepo, SlurmRepo
from hpc_agent.tools.base import Risk, get_tool, tool
from hpc_agent.tools.errors import ErrorKind, ToolError
from hpc_agent.tools.result import ToolResult
from hpc_agent.tools.slurm_parse import (
    minutes_to_slurm_time,
    parse_pipe_table,
    slurm_time_to_minutes,
)

SACCTMGR = f"{settings.slurm_bin_dir}/sacctmgr"
SCONTROL = f"{settings.slurm_bin_dir}/scontrol"
SQUEUE = f"{settings.slurm_bin_dir}/squeue"
SACCT = f"{settings.slurm_bin_dir}/sacct"
SREPORT = f"{settings.slurm_bin_dir}/sreport"
SDIAG = f"{settings.slurm_bin_dir}/sdiag"


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


class ExtendAccountIn(BaseModel):
    name: str
    op: Literal["create", "modify"]
    parent: str | None = None
    organization: str | None = None
    description: str | None = None
    grp_tres: str | None = None
    max_wall_min: int | None = None
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


class SetLimitsIn(BaseModel):
    target: Literal["account", "qos", "user_assoc"]
    name: str | None = None
    user: str | None = None
    account: str | None = None
    max_wall_min: int | None = None
    grp_tres: str | None = None
    max_tres: str | None = None
    max_jobs_pu: int | None = None
    qos_list: list[str] | None = None
    qos_add: list[str] | None = None
    default_qos: str | None = None
    fairshare: int | None = None
    dry_run: bool = True


class NodeStateIn(BaseModel):
    node: str
    target: Literal["drain", "resume", "down", "undrain"]
    reason: str | None = None
    dry_run: bool = True


class ReconfigureIn(BaseModel):
    dry_run: bool = True


class NodeStatusIn(BaseModel):
    node: str | None = None
    reconcile_state: bool = True


class ShowAssocIn(BaseModel):
    user: str | None = None
    account: str | None = None


class QueueIn(BaseModel):
    user: str | None = None
    partition: str | None = None


class JobAccountingIn(BaseModel):
    user: str | None = None
    account: str | None = None
    start: str | None = None
    end: str | None = None
    state: str | None = None


class UsageReportIn(BaseModel):
    start: str | None = None
    end: str | None = None
    account: str | None = None
    user: str | None = None


class DiagIn(BaseModel):
    include_config: bool = True
    include_sdiag: bool = True


class ManageReservationIn(BaseModel):
    name: str
    op: Literal["create", "delete"]
    nodes: list[str] | None = None
    start: str | None = None
    duration_min: int | None = None
    users: list[str] | None = None
    flags: list[str] = Field(default_factory=lambda: ["MAINT", "IGNORE_JOBS"])
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

_ACCOUNT_FIELD_MAP: dict[str, tuple[str, str]] = {
    "parent": ("Parent", "ParentName"),
    "organization": ("Organization", "Org"),
    "description": ("Description", "Descr"),
    "grp_tres": ("GrpTRES", "GrpTRES"),
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


def _read_account(name: str, *, actor: str, audit_id: str) -> dict[str, str] | None:
    spec = CommandSpec(
        argv=[SACCTMGR, "show", "account", name, "-P", "--noheader=no"],
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


def _read_assoc_rows(inp: ShowAssocIn, *, actor: str, audit_id: str) -> list[dict[str, str]] | None:
    argv = [SACCTMGR, "show", "assoc"]
    if inp.user is not None:
        argv.append(f"user={inp.user}")
    if inp.account is not None:
        argv.append(f"account={inp.account}")
    argv.extend(["-P", "--noheader=no"])
    res = run_command(CommandSpec(argv=argv, timeout_s=30), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    return parse_pipe_table(res.stdout)


def _qos_exists(name: str, *, actor: str, audit_id: str) -> bool:
    return _read_current(name, actor=actor, audit_id=audit_id) is not None


def _read_node(inp: NodeStateIn, *, actor: str, audit_id: str) -> dict[str, object] | None:
    spec = CommandSpec(
        argv=[SCONTROL, "show", "node", inp.node, "--json"],
        timeout_s=30,
    )
    res = run_command(spec, actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    first = nodes[0]
    return first if isinstance(first, dict) else None


def _read_nodes(inp: NodeStatusIn, *, actor: str, audit_id: str) -> list[dict[str, object]] | None:
    argv = [SCONTROL, "show", "node"]
    if inp.node is not None:
        argv.append(inp.node)
    argv.append("--json")
    res = run_command(CommandSpec(argv=argv, timeout_s=30), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        return None
    return [node for node in nodes if isinstance(node, dict)]


def _read_reservation(name: str, *, actor: str, audit_id: str) -> dict[str, object] | None:
    res = run_command(
        CommandSpec(argv=[SCONTROL, "show", "reservation", name, "--json"], timeout_s=30),
        actor=actor,
        audit_id=audit_id,
    )
    if res.rc != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    reservations = data.get("reservations")
    if not isinstance(reservations, list) or not reservations:
        return None
    first = reservations[0]
    return first if isinstance(first, dict) else None


def _read_queue(inp: QueueIn, *, actor: str, audit_id: str) -> list[dict[str, object]] | None:
    argv = [SQUEUE, "--json"]
    if inp.user is not None:
        argv.append(f"--user={inp.user}")
    if inp.partition is not None:
        argv.append(f"--partition={inp.partition}")
    res = run_command(CommandSpec(argv=argv, timeout_s=30), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return None
    return [job for job in jobs if isinstance(job, dict)]


def _read_job_accounting(
    inp: JobAccountingIn, *, actor: str, audit_id: str
) -> list[dict[str, object]] | None:
    argv = [SACCT, "--json"]
    if inp.user is not None:
        argv.append(f"--user={inp.user}")
    if inp.account is not None:
        argv.append(f"--account={inp.account}")
    if inp.start is not None:
        argv.append(f"--starttime={inp.start}")
    if inp.end is not None:
        argv.append(f"--endtime={inp.end}")
    if inp.state is not None:
        argv.append(f"--state={inp.state}")
    res = run_command(CommandSpec(argv=argv, timeout_s=30), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        return None
    return [job for job in jobs if isinstance(job, dict)]


def _read_usage_report(
    inp: UsageReportIn, *, actor: str, audit_id: str
) -> list[dict[str, str]] | None:
    argv = [SREPORT, "cluster", "user", "Utilization", "-P"]
    if inp.start is not None:
        argv.append(f"Start={inp.start}")
    if inp.end is not None:
        argv.append(f"End={inp.end}")
    if inp.account is not None:
        argv.append(f"Accounts={inp.account}")
    if inp.user is not None:
        argv.append(f"Users={inp.user}")
    res = run_command(CommandSpec(argv=argv, timeout_s=30), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        return None
    return parse_pipe_table(res.stdout)


def _parse_key_value_lines(text: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
        elif ":" in stripped:
            key, value = stripped.split(":", 1)
        else:
            continue
        parsed[key.strip()] = value.strip()
    return parsed


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


def _account_desired_changes(inp: ExtendAccountIn, current: dict[str, str] | None) -> list[Change]:
    changes: list[Change] = []
    target = f"account/{inp.name}"

    if inp.op == "create" and current is None:
        changes.append(Change(target=target, op="create", after=inp.name))

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

    for field_name, (_set_key, show_col) in _ACCOUNT_FIELD_MAP.items():
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


def _node_state_tokens(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item).upper() for item in value]
    if isinstance(value, str):
        return [part.strip().upper() for part in value.replace("+", ",").split(",") if part.strip()]
    return []


def _node_state_label(current: dict[str, object] | None) -> str | None:
    if current is None:
        return None
    tokens = _node_state_tokens(current.get("state"))
    return ",".join(tokens) if tokens else None


def _node_reason(current: dict[str, object] | None) -> str | None:
    if current is None:
        return None
    reason = current.get("reason")
    return str(reason) if reason not in (None, "") else None


def _node_name(current: dict[str, object]) -> str | None:
    for key in ("name", "hostname", "node_name"):
        value = current.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _slurm_state_to_model(current: dict[str, object]) -> NodeState:
    tokens = set(_node_state_tokens(current.get("state")))
    if tokens.intersection({"DRAIN", "DRAINED", "DRAINING"}):
        return NodeState.DRAINED
    if "DOWN" in tokens:
        return NodeState.DOWN
    if tokens.intersection({"ALLOCATED", "COMPLETING", "IDLE", "MIXED"}):
        return NodeState.UP
    return NodeState.UNKNOWN


def _node_status_row(current: dict[str, object]) -> dict[str, object]:
    return {
        "node": _node_name(current),
        "state": _node_state_label(current),
        "reason": _node_reason(current),
        "features": current.get("features") or current.get("available_features"),
        "gres": current.get("gres"),
    }


def _queue_job_row(job: dict[str, object]) -> dict[str, object]:
    return {
        "job_id": job.get("job_id") or job.get("jobid"),
        "name": job.get("name"),
        "user": job.get("user_name") or job.get("user"),
        "partition": job.get("partition"),
        "state": job.get("job_state") or job.get("state"),
        "nodes": job.get("nodes"),
        "reason": job.get("state_reason") or job.get("reason"),
    }


def _accounting_job_row(job: dict[str, object]) -> dict[str, object]:
    return {
        "job_id": job.get("job_id") or job.get("jobid"),
        "name": job.get("name"),
        "user": job.get("user") or job.get("user_name"),
        "account": job.get("account"),
        "partition": job.get("partition"),
        "state": job.get("state") or job.get("job_state"),
        "elapsed": job.get("elapsed"),
        "start": job.get("start"),
        "end": job.get("end"),
        "exit_code": job.get("exit_code"),
    }


def _usage_report_row(row: dict[str, str]) -> dict[str, str | float | None]:
    normalized: dict[str, str | float | None] = {
        "cluster": row.get("Cluster"),
        "login": row.get("Login"),
        "proper_name": row.get("Proper Name"),
        "account": row.get("Account"),
        "used": row.get("Used"),
        "energy": row.get("Energy"),
    }
    for key, value in row.items():
        normalized.setdefault(key, value)
    return normalized


def _reservation_csv(value: object) -> str | None:
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    if value not in (None, ""):
        return str(value)
    return None


def _reservation_desired_changes(
    inp: ManageReservationIn, current: dict[str, object] | None
) -> list[Change]:
    target = f"reservation/{inp.name}"
    if inp.op == "delete":
        if current is None:
            return []
        return [Change(target=target, op="delete", before=inp.name)]
    if current is not None:
        return []
    changes = [Change(target=target, op="create", after=inp.name)]
    if inp.nodes is not None:
        changes.append(Change(target=target, field="nodes", op="modify", after=",".join(inp.nodes)))
    if inp.start is not None:
        changes.append(Change(target=target, field="start", op="modify", after=inp.start))
    if inp.duration_min is not None:
        changes.append(
            Change(target=target, field="duration_min", op="modify", after=str(inp.duration_min))
        )
    if inp.users is not None:
        changes.append(Change(target=target, field="users", op="modify", after=",".join(inp.users)))
    if inp.flags:
        changes.append(Change(target=target, field="flags", op="modify", after=",".join(inp.flags)))
    return changes


def _is_node_target_noop(inp: NodeStateIn, current: dict[str, object] | None) -> bool:
    tokens = set(_node_state_tokens(current.get("state") if current else None))
    if inp.target == "drain":
        return bool(tokens.intersection({"DRAIN", "DRAINED", "DRAINING"}))
    if inp.target == "down":
        return "DOWN" in tokens
    return not tokens.intersection({"DRAIN", "DRAINED", "DRAINING", "DOWN"})


def _node_target_state(inp: NodeStateIn) -> str:
    return {
        "drain": "DRAIN",
        "down": "DOWN",
        "resume": "RESUME",
        "undrain": "UNDRAIN",
    }[inp.target]


def _node_desired_changes(inp: NodeStateIn, current: dict[str, object] | None) -> list[Change]:
    if _is_node_target_noop(inp, current):
        return []
    before = _node_state_label(current)
    after = _node_target_state(inp)
    op = (
        "drain"
        if inp.target == "drain"
        else "resume" if inp.target in {"resume", "undrain"} else "modify"
    )
    changes = [
        Change(
            target=f"node/{inp.node}",
            field="state",
            op=op,
            before=before,
            after=after,
        )
    ]
    if inp.reason is not None and inp.target in {"drain", "down"}:
        changes.append(
            Change(
                target=f"node/{inp.node}",
                field="reason",
                op="modify",
                before=_node_reason(current),
                after=inp.reason,
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


def _assoc_desired_changes(inp: ManageUserAssocIn, current: dict[str, str] | None) -> list[Change]:
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


def _build_account_tokens(inp: ExtendAccountIn) -> list[str]:
    tokens: list[str] = []
    if inp.max_wall_min is not None:
        tokens.append(f"MaxWall={minutes_to_slurm_time(inp.max_wall_min)}")
    for field_name, (set_key, _col) in _ACCOUNT_FIELD_MAP.items():
        val = getattr(inp, field_name)
        if val is not None:
            tokens.append(f"{set_key}={val}")
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


def _account_forward_argv(inp: ExtendAccountIn) -> list[str]:
    tokens = _build_account_tokens(inp)
    if inp.op == "create":
        return [SACCTMGR, "-i", "add", "account", inp.name, *tokens]
    return [SACCTMGR, "-i", "modify", "account", inp.name, "set", *tokens]


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


def _node_forward_argv(inp: NodeStateIn) -> list[str]:
    argv = [
        SCONTROL,
        "update",
        f"NodeName={inp.node}",
        f"State={_node_target_state(inp)}",
    ]
    if inp.target in {"drain", "down"} and inp.reason is not None:
        argv.append(f"Reason={inp.reason}")
    return argv


def _reservation_forward_argv(inp: ManageReservationIn) -> list[str]:
    if inp.op == "delete":
        return [SCONTROL, "delete", f"reservation={inp.name}"]
    argv = [SCONTROL, "create", "reservation", f"ReservationName={inp.name}"]
    if inp.nodes is not None:
        argv.append(f"Nodes={','.join(inp.nodes)}")
    if inp.start is not None:
        argv.append(f"StartTime={inp.start}")
    if inp.duration_min is not None:
        argv.append(f"Duration={inp.duration_min}")
    if inp.users is not None:
        argv.append(f"Users={','.join(inp.users)}")
    if inp.flags:
        argv.append(f"Flags={','.join(inp.flags)}")
    return argv


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


def _account_inverse_argv(inp: ExtendAccountIn, current: dict[str, str] | None) -> list[list[str]]:
    if inp.op == "create":
        return [[SACCTMGR, "-i", "delete", "account", inp.name]]
    if current is None:
        return []
    restore: list[str] = []
    if inp.max_wall_min is not None:
        restore.append(f"MaxWall={current.get('MaxWall') or '-1'}")
    for field_name, (set_key, col) in _ACCOUNT_FIELD_MAP.items():
        if getattr(inp, field_name) is not None:
            restore.append(f"{set_key}={current.get(col) or '-1'}")
    if not restore:
        return []
    return [[SACCTMGR, "-i", "modify", "account", inp.name, "set", *restore]]


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


def _node_inverse_argv(inp: NodeStateIn) -> list[list[str]]:
    if inp.target in {"drain", "down"}:
        return [[SCONTROL, "update", f"NodeName={inp.node}", "State=RESUME"]]
    return []


def _reservation_inverse_argv(
    inp: ManageReservationIn, current: dict[str, object] | None
) -> list[list[str]]:
    if inp.op == "create":
        return [[SCONTROL, "delete", f"reservation={inp.name}"]]
    if current is None:
        return []
    argv = [SCONTROL, "create", "reservation", f"ReservationName={inp.name}"]
    nodes = _reservation_csv(current.get("nodes"))
    users = _reservation_csv(current.get("users"))
    flags = _reservation_csv(current.get("flags"))
    start = current.get("start_time") or current.get("start")
    duration = current.get("duration") or current.get("duration_minutes")
    if nodes:
        argv.append(f"Nodes={nodes}")
    if start:
        argv.append(f"StartTime={start}")
    if duration:
        argv.append(f"Duration={duration}")
    if users:
        argv.append(f"Users={users}")
    if flags:
        argv.append(f"Flags={flags}")
    return [argv]


def _blast_radius(inp: BaseModel) -> int:
    return 1  # a single QOS


def _account_blast_radius(inp: BaseModel) -> int:
    return 1  # a single account


def _limits_blast_radius(inp: BaseModel) -> int:
    return 1  # one target object


def _assoc_blast_radius(inp: BaseModel) -> int:
    return 1  # a single user/account association


def _node_blast_radius(inp: BaseModel) -> int:
    return 1  # a single node


def _reconfigure_blast_radius(inp: BaseModel) -> int:
    return 1  # a controller re-read, not a data mutation


def _reservation_blast_radius(inp: BaseModel) -> int:
    return 1  # a single reservation


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


def _persist_account(inp: ExtendAccountIn) -> None:
    fields: dict[str, object] = {}
    if inp.parent is not None:
        fields["parent"] = inp.parent
    if inp.organization is not None:
        fields["organization"] = inp.organization
    if inp.description is not None:
        fields["description"] = inp.description
    try:
        with session_scope() as s:
            SlurmRepo(s).upsert_account(inp.name, **fields)
    except Exception:  # noqa: BLE001 - state persistence is best-effort; audit is canonical
        pass


def _persist_node_state(inp: NodeStateIn) -> None:
    target_state = {
        "drain": NodeState.DRAINED,
        "down": NodeState.DOWN,
        "resume": NodeState.UP,
        "undrain": NodeState.UP,
    }[inp.target]
    try:
        with session_scope() as s:
            repo = NodeRepo(s)
            node = repo.get(inp.node)
            if node is not None:
                repo.upsert(inp.node, state=target_state)
    except Exception:  # noqa: BLE001 - state persistence is best-effort; audit is canonical
        pass


def _reconcile_node_status(nodes: list[dict[str, object]]) -> None:
    try:
        with session_scope() as s:
            repo = NodeRepo(s)
            for row in nodes:
                name = _node_name(row)
                if name is None or repo.get(name) is None:
                    continue
                repo.upsert(name, state=_slurm_state_to_model(row))
    except Exception:  # noqa: BLE001 - state persistence is best-effort; query data is canonical
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
    name="slurm.set_limits",
    risk=Risk.MEDIUM,
    domain="slurm",
    blast_radius=_limits_blast_radius,
)
def set_limits(
    inp: SetLimitsIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
    persist_state: bool = True,
) -> ToolResult:
    """Set limits on an existing account, QOS, or user/account association."""
    if inp.target == "qos":
        if inp.name is None:
            return ToolResult.failed(
                ToolError(kind=ErrorKind.PRECONDITION, message="qos name required")
            )
        return manage_qos(
            ManageQOSIn(
                name=inp.name,
                op="modify",
                max_wall_min=inp.max_wall_min,
                max_jobs_pu=inp.max_jobs_pu,
                max_tres=inp.max_tres,
                grp_tres=inp.grp_tres,
                dry_run=inp.dry_run,
            ),
            actor=actor,
            actor_role=actor_role,
            policy=policy,
            gate_override=gate_override,
            persist_state=persist_state,
        )
    if inp.target == "account":
        if inp.name is None:
            return ToolResult.failed(
                ToolError(kind=ErrorKind.PRECONDITION, message="account name required")
            )
        return extend_account(
            ExtendAccountIn(
                name=inp.name,
                op="modify",
                max_wall_min=inp.max_wall_min,
                grp_tres=inp.grp_tres,
                dry_run=inp.dry_run,
            ),
            actor=actor,
            actor_role=actor_role,
            policy=policy,
            gate_override=gate_override,
            persist_state=persist_state,
        )
    if inp.user is None or inp.account is None:
        return ToolResult.failed(
            ToolError(kind=ErrorKind.PRECONDITION, message="user and account required")
        )
    return manage_user_assoc(
        ManageUserAssocIn(
            user=inp.user,
            account=inp.account,
            op="modify",
            qos_list=inp.qos_list,
            qos_add=inp.qos_add,
            default_qos=inp.default_qos,
            fairshare=inp.fairshare,
            dry_run=inp.dry_run,
        ),
        actor=actor,
        actor_role=actor_role,
        policy=policy,
        gate_override=gate_override,
        persist_state=persist_state,
    )


@tool(
    name="slurm.extend_account",
    risk=Risk.MEDIUM,
    domain="slurm",
    blast_radius=_account_blast_radius,
)
def extend_account(
    inp: ExtendAccountIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
    persist_state: bool = True,
) -> ToolResult:
    """Create or modify a Slurm account and account-level limits."""
    meta, _fn, _br = get_tool("slurm.extend_account")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        if inp.op == "modify" and not _build_account_tokens(inp):
            raise _precondition("modify requires at least one field to change")

        current = _read_account(inp.name, actor=actor, audit_id=audit_id)
        if inp.op == "modify" and current is None:
            raise _err(
                ErrorKind.NOT_FOUND,
                f"account '{inp.name}' does not exist",
                "create it first",
            )

        changes = _account_desired_changes(inp, current)
        forward = _account_forward_argv(inp)
        diff = Diff(
            changes=changes,
            commands_preview=[redacted_argv(CommandSpec(argv=forward))],
            blast_radius=_account_blast_radius(inp),
            reversible=(inp.op == "modify"),
            revert_hint=None if inp.op == "modify" else "create is reverted only by manual delete",
        )
        if diff.is_noop():
            event.decision = "auto"
            event.diff_summary = "no-op"
            event.result_status = "ok"
            audit.commit_event(event)
            return ToolResult.success(data={"account": inp.name, "noop": True}, audit_id=audit_id)

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
                    remediation="check account hierarchy and limit syntax",
                )
            )

        event.decision = _decision_str(g)
        event.diff_summary = diff.render()
        event.result_status = "ok"
        event.revert_argv = _account_inverse_argv(inp, current)
        audit.commit_event(event)

        if persist_state:
            _persist_account(inp)

        return ToolResult.success(
            data={"account": inp.name, "applied": True},
            diff=diff,
            audit_id=audit_id,
        )

    except _ToolBoundaryError as exc:
        event.result_status = "error"
        event.decision = "error"
        audit.commit_event(event)
        return ToolResult.failed(exc.error)


@tool(name="slurm.node_status", risk=Risk.READ, domain="slurm", blast_radius=_node_blast_radius)
def node_status(
    inp: NodeStatusIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
    persist_state: bool = True,
) -> ToolResult:
    """Return Slurm node state and reason from scontrol JSON output."""
    meta, _fn, _br = get_tool("slurm.node_status")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id

    diff = Diff(changes=[], blast_radius=_node_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    nodes = _read_nodes(inp, actor=actor, audit_id=audit_id)
    if nodes is None:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message="scontrol show node failed or returned invalid JSON",
                remediation="check Slurm controller health and scontrol JSON support",
            )
        )
    if inp.node is not None and not nodes:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.NOT_FOUND,
                message=f"node '{inp.node}' does not exist",
                remediation="check node name",
            )
        )

    if persist_state and inp.reconcile_state:
        _reconcile_node_status(nodes)

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"nodes": [_node_status_row(node) for node in nodes]},
        audit_id=audit_id,
    )


@tool(name="slurm.show_assoc", risk=Risk.READ, domain="slurm", blast_radius=_assoc_blast_radius)
def show_assoc(
    inp: ShowAssocIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Return Slurm user/account associations from sacctmgr parseable output."""
    meta, _fn, _br = get_tool("slurm.show_assoc")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id

    diff = Diff(changes=[], blast_radius=_assoc_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    rows = _read_assoc_rows(inp, actor=actor, audit_id=audit_id)
    if rows is None:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message="sacctmgr show assoc failed",
                remediation="check slurmdbd health and sacctmgr access",
            )
        )

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(data={"associations": rows}, audit_id=audit_id)


@tool(name="slurm.queue", risk=Risk.READ, domain="slurm", blast_radius=_node_blast_radius)
def queue(
    inp: QueueIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Return Slurm queue jobs from squeue JSON output."""
    meta, _fn, _br = get_tool("slurm.queue")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id

    diff = Diff(changes=[], blast_radius=_node_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    jobs = _read_queue(inp, actor=actor, audit_id=audit_id)
    if jobs is None:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message="squeue failed or returned invalid JSON",
                remediation="check Slurm controller health and squeue JSON support",
            )
        )

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"jobs": [_queue_job_row(job) for job in jobs]},
        audit_id=audit_id,
    )


@tool(name="slurm.job_accounting", risk=Risk.READ, domain="slurm", blast_radius=_node_blast_radius)
def job_accounting(
    inp: JobAccountingIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Return completed Slurm job accounting records from sacct JSON output."""
    meta, _fn, _br = get_tool("slurm.job_accounting")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id

    diff = Diff(changes=[], blast_radius=_node_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    jobs = _read_job_accounting(inp, actor=actor, audit_id=audit_id)
    if jobs is None:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message="sacct failed or returned invalid JSON",
                remediation="check slurmdbd health and sacct JSON support",
            )
        )

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"jobs": [_accounting_job_row(job) for job in jobs]},
        audit_id=audit_id,
    )


@tool(name="slurm.usage_report", risk=Risk.READ, domain="slurm", blast_radius=_node_blast_radius)
def usage_report(
    inp: UsageReportIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Return Slurm user/account utilization from sreport parseable output."""
    meta, _fn, _br = get_tool("slurm.usage_report")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id

    diff = Diff(changes=[], blast_radius=_node_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    rows = _read_usage_report(inp, actor=actor, audit_id=audit_id)
    if rows is None:
        event.decision = "auto"
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message="sreport failed",
                remediation="check slurmdbd health and sreport access",
            )
        )

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(
        data={"usage": [_usage_report_row(row) for row in rows]},
        audit_id=audit_id,
    )


@tool(name="slurm.diag", risk=Risk.READ, domain="slurm", blast_radius=_node_blast_radius)
def diag(
    inp: DiagIn,
    *,
    actor: str,
    actor_role: Role = Role.VIEWER,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Return Slurm controller diagnostic/config information."""
    meta, _fn, _br = get_tool("slurm.diag")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(),
    )
    audit_id = event.id

    diff = Diff(changes=[], blast_radius=_node_blast_radius(inp), reversible=True)
    g = safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="read"
    )
    if g.denied:
        event.decision = "denied"
        event.result_status = "denied"
        audit.commit_event(event)
        return ToolResult.denied(g.reason or "denied by policy")

    data: dict[str, object] = {}
    if inp.include_config:
        config = run_command(
            CommandSpec(argv=[SCONTROL, "show", "config"], timeout_s=30),
            actor=actor,
            audit_id=audit_id,
        )
        if config.rc != 0:
            event.decision = "auto"
            event.result_status = "error"
            audit.commit_event(event)
            return ToolResult.failed(
                ToolError(
                    kind=ErrorKind.COMMAND_FAILED,
                    message=f"scontrol show config failed (rc={config.rc})",
                    detail=config.stderr,
                    remediation="check Slurm controller health",
                )
            )
        data["config"] = _parse_key_value_lines(config.stdout)

    if inp.include_sdiag:
        sdiag = run_command(
            CommandSpec(argv=[SDIAG], timeout_s=30),
            actor=actor,
            audit_id=audit_id,
        )
        if sdiag.rc != 0:
            event.decision = "auto"
            event.result_status = "error"
            audit.commit_event(event)
            return ToolResult.failed(
                ToolError(
                    kind=ErrorKind.COMMAND_FAILED,
                    message=f"sdiag failed (rc={sdiag.rc})",
                    detail=sdiag.stderr,
                    remediation="check Slurm controller health",
                )
            )
        data["sdiag"] = _parse_key_value_lines(sdiag.stdout)

    event.decision = "auto"
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(data=data, audit_id=audit_id)


@tool(
    name="slurm.manage_reservation",
    risk=Risk.MEDIUM,
    domain="slurm",
    blast_radius=_reservation_blast_radius,
)
def manage_reservation(
    inp: ManageReservationIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Create or delete a Slurm maintenance reservation."""
    meta, _fn, _br = get_tool("slurm.manage_reservation")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        if inp.op == "create" and (not inp.nodes or inp.start is None or inp.duration_min is None):
            raise _precondition("create requires nodes, start, and duration_min")

        current = _read_reservation(inp.name, actor=actor, audit_id=audit_id)
        if inp.op == "delete" and current is None:
            raise _err(
                ErrorKind.NOT_FOUND,
                f"reservation '{inp.name}' does not exist",
                "check reservation name",
            )

        changes = _reservation_desired_changes(inp, current)
        forward = _reservation_forward_argv(inp)
        inverse = _reservation_inverse_argv(inp, current)
        diff = Diff(
            changes=changes,
            commands_preview=[redacted_argv(CommandSpec(argv=forward))],
            blast_radius=_reservation_blast_radius(inp),
            reversible=bool(inverse),
            revert_hint=None if inverse else "no inverse command could be derived",
        )
        if diff.is_noop():
            event.decision = "auto"
            event.diff_summary = "no-op"
            event.result_status = "ok"
            audit.commit_event(event)
            return ToolResult.success(
                data={"reservation": inp.name, "noop": True},
                audit_id=audit_id,
            )

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

        res = run_command(CommandSpec(argv=forward, timeout_s=60), actor=actor, audit_id=audit_id)
        if res.rc != 0:
            event.decision = _decision_str(g)
            event.diff_summary = diff.render()
            event.result_status = "error"
            audit.commit_event(event)
            return ToolResult.failed(
                ToolError(
                    kind=ErrorKind.COMMAND_FAILED,
                    message=f"scontrol failed (rc={res.rc})",
                    detail=res.stderr,
                    remediation="check reservation syntax and Slurm controller health",
                )
            )

        event.decision = _decision_str(g)
        event.diff_summary = diff.render()
        event.result_status = "ok"
        event.revert_argv = inverse
        audit.commit_event(event)
        return ToolResult.success(
            data={"reservation": inp.name, "op": inp.op},
            diff=diff,
            audit_id=audit_id,
        )

    except _ToolBoundaryError as exc:
        event.result_status = "error"
        event.decision = "error"
        audit.commit_event(event)
        return ToolResult.failed(exc.error)


@tool(
    name="slurm.reconfigure",
    risk=Risk.LOW,
    domain="slurm",
    blast_radius=_reconfigure_blast_radius,
)
def reconfigure(
    inp: ReconfigureIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
) -> ToolResult:
    """Ask the Slurm controller to re-read its configuration."""
    meta, _fn, _br = get_tool("slurm.reconfigure")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id
    forward = [SCONTROL, "reconfigure"]
    diff = Diff(
        changes=[
            Change(
                target="slurm/controller",
                field="configuration",
                op="modify",
                before="loaded",
                after="reload requested",
            )
        ],
        commands_preview=[redacted_argv(CommandSpec(argv=forward))],
        blast_radius=_reconfigure_blast_radius(inp),
        reversible=False,
        revert_hint="reconfigure cannot be reverted; restore config and reconfigure again",
    )

    g = gate_override or safety_gate.evaluate(
        meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op="reconfigure"
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

    res = run_command(CommandSpec(argv=forward, timeout_s=60), actor=actor, audit_id=audit_id)
    if res.rc != 0:
        event.decision = _decision_str(g)
        event.diff_summary = diff.render()
        event.result_status = "error"
        audit.commit_event(event)
        return ToolResult.failed(
            ToolError(
                kind=ErrorKind.COMMAND_FAILED,
                message=f"scontrol reconfigure failed (rc={res.rc})",
                detail=res.stderr,
                remediation="run slurmctld config validation and inspect controller logs",
            )
        )

    event.decision = _decision_str(g)
    event.diff_summary = diff.render()
    event.result_status = "ok"
    audit.commit_event(event)
    return ToolResult.success(data={"reconfigured": True}, diff=diff, audit_id=audit_id)


@tool(name="slurm.node_state", risk=Risk.MEDIUM, domain="slurm", blast_radius=_node_blast_radius)
def node_state(
    inp: NodeStateIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
    gate_override: safety_gate.Gate | None = None,
    persist_state: bool = True,
) -> ToolResult:
    """Drain, resume, down, or undrain a Slurm node."""
    meta, _fn, _br = get_tool("slurm.node_state")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        if inp.target in {"drain", "down"} and not inp.reason:
            raise _precondition(f"{inp.target} requires a reason")

        current = _read_node(inp, actor=actor, audit_id=audit_id)
        if current is None:
            raise _err(ErrorKind.NOT_FOUND, f"node '{inp.node}' does not exist", "check node name")

        changes = _node_desired_changes(inp, current)
        forward = _node_forward_argv(inp)
        diff = Diff(
            changes=changes,
            commands_preview=[redacted_argv(CommandSpec(argv=forward))],
            blast_radius=_node_blast_radius(inp),
            reversible=bool(_node_inverse_argv(inp)),
            revert_hint=(
                None if _node_inverse_argv(inp) else "resume/undrain has no recorded inverse"
            ),
        )
        if diff.is_noop():
            event.decision = "auto"
            event.diff_summary = "no-op"
            event.result_status = "ok"
            audit.commit_event(event)
            return ToolResult.success(data={"node": inp.node, "noop": True}, audit_id=audit_id)

        g = gate_override or safety_gate.evaluate(
            meta, inp.model_dump(), diff, actor_role=actor_role, policy=policy, op=inp.target
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

        res = run_command(CommandSpec(argv=forward, timeout_s=60), actor=actor, audit_id=audit_id)
        if res.rc != 0:
            event.decision = _decision_str(g)
            event.diff_summary = diff.render()
            event.result_status = "error"
            audit.commit_event(event)
            return ToolResult.failed(
                ToolError(
                    kind=ErrorKind.COMMAND_FAILED,
                    message=f"scontrol failed (rc={res.rc})",
                    detail=res.stderr,
                    remediation="check node name and Slurm controller health",
                )
            )

        event.decision = _decision_str(g)
        event.diff_summary = diff.render()
        event.result_status = "ok"
        event.revert_argv = _node_inverse_argv(inp)
        audit.commit_event(event)

        if persist_state:
            _persist_node_state(inp)

        return ToolResult.success(
            data={"node": inp.node, "state": inp.target},
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


# --- Partition tools (spec 05 §2.1-2.2) ---


class ManagePartitionIn(BaseModel):
    name: str
    op: Literal["create", "modify"]
    nodes: list[str] | None = None
    default: bool | None = None
    max_time_min: int | None = None
    default_qos: str | None = None
    allow_qos: list[str] | None = None
    state: Literal["UP", "DOWN", "DRAIN"] | None = None
    dry_run: bool = True


class AddNodeToPartitionIn(BaseModel):
    node: str
    partition: str
    features: list[str] = []
    gres: str | None = None
    dry_run: bool = True


def _partition_blast_radius(inp: BaseModel) -> int:
    if isinstance(inp, ManagePartitionIn):
        return 1
    if isinstance(inp, AddNodeToPartitionIn):
        return 1
    return 0


def _read_slurm_conf(selector: str, actor: str, audit_id: str) -> str:
    """Read slurm.conf from config repo."""
    from hpc_agent.state.configrepo import get_config_repo

    repo = get_config_repo()
    try:
        return repo.read("slurm/slurm.conf")
    except FileNotFoundError:
        return ""


def _parse_partition_slurm_line(line: str) -> dict[str, Any]:
    """Parse a PartitionName line from slurm.conf."""
    result: dict[str, Any] = {}
    parts = line.split()
    for part in parts:
        if "=" in part:
            key, val = part.split("=", 1)
            if key == "Nodes":
                result["nodes"] = val.split(",")
            elif key == "MaxTime":
                result["max_time"] = val
            elif key == "DefQOS":
                result["default_qos"] = val
            elif key == "AllowQOS":
                result["allow_qos"] = val.split(",")
            elif key == "State":
                result["state"] = val
    return result


def _build_partition_line(name: str, cfg: dict[str, Any]) -> str:
    """Build a PartitionName line from config."""
    parts = [f"PartitionName={name}"]
    if cfg.get("nodes"):
        parts.append(f"Nodes={','.join(cfg['nodes'])}")
    if cfg.get("max_time"):
        parts.append(f"MaxTime={cfg['max_time']}")
    if cfg.get("default_qos"):
        parts.append(f"DefQOS={cfg['default_qos']}")
    if cfg.get("allow_qos"):
        parts.append(f"AllowQOS={','.join(cfg['allow_qos'])}")
    if cfg.get("default"):
        parts.append("Default=YES")
    if cfg.get("state"):
        parts.append(f"State={cfg['state']}")
    return " ".join(parts)


@tool(
    name="slurm.manage_partition",
    risk=Risk.MEDIUM,
    domain="slurm",
    blast_radius=_partition_blast_radius,
)
def manage_partition(
    inp: ManagePartitionIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Create or modify a Slurm partition definition in slurm.conf.

    This tool edits the slurm.conf file in the config repo and
    triggers slurmctld reconfigure to apply changes.
    """
    meta, _fn, _br = get_tool("slurm.manage_partition")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        from hpc_agent.state.configrepo import get_config_repo

        repo = get_config_repo()

        # Read current slurm.conf
        current_conf = _read_slurm_conf("partition", actor, audit_id)

        # Parse existing partition or prepare new definition
        lines = current_conf.splitlines()
        partition_lines = [line for line in lines if line.startswith("PartitionName=")]
        existing_cfg: dict[str, Any] = {}

        for line in partition_lines:
            if line.startswith(f"PartitionName={inp.name}"):
                existing_cfg = _parse_partition_slurm_line(line)
                break

        # Compute desired state
        desired_cfg = dict(existing_cfg)
        if inp.nodes is not None:
            desired_cfg["nodes"] = inp.nodes
        if inp.default is not None:
            desired_cfg["default"] = inp.default
        if inp.max_time_min is not None:
            # Convert minutes to D-HH:MM:SS format
            days = inp.max_time_min // 1440
            hours = (inp.max_time_min % 1440) // 60
            minutes = inp.max_time_min % 60
            if days:
                desired_cfg["max_time"] = f"{days}-{hours:02}:{minutes:02}:00"
            else:
                desired_cfg["max_time"] = f"{hours:02}:{minutes:02}:00"
        if inp.default_qos is not None:
            desired_cfg["default_qos"] = inp.default_qos
        if inp.allow_qos is not None:
            desired_cfg["allow_qos"] = inp.allow_qos
        if inp.state is not None:
            desired_cfg["state"] = inp.state

        # Build new partition line
        new_line = _build_partition_line(inp.name, desired_cfg)

        # Update configuration
        new_lines = []
        updated = False
        for line in lines:
            if line.startswith(f"PartitionName={inp.name}"):
                new_lines.append(new_line)
                updated = True
            else:
                new_lines.append(line)

        if not updated:
            new_lines.append(new_line)

        # Write new config
        new_conf = "\n".join(new_lines)

        # Validate configuration
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".slurm.conf", delete=False) as tmp:
            tmp.write(new_conf)
            tmp_path = tmp.name

        try:
            validate_cmd = [f"{settings.slurm_bin_dir}/slurmctld", "-t", "-f", tmp_path]
            res = run_command(
                CommandSpec(argv=validate_cmd, timeout_s=30),
                actor=actor,
                audit_id=audit_id,
            )
            if res.rc != 0:
                raise _precondition(f"slurm.conf validation failed: {res.stderr}")
        finally:
            os.unlink(tmp_path)

        # Compute diff
        changes = []
        if existing_cfg:
            if desired_cfg.get("max_time") != existing_cfg.get("max_time"):
                changes.append(
                    Change(
                        target=f"partition/{inp.name}",
                        field="max_time_min",
                        before=existing_cfg.get("max_time"),
                        after=desired_cfg.get("max_time"),
                        op="modify",
                    )
                )
        else:
            changes.append(
                Change(
                    target=f"partition/{inp.name}",
                    field=None,
                    before=None,
                    after="created",
                    op="create",
                )
            )

        diff = Diff(
            changes=changes,
            commands_preview=[
                redacted_argv(
                    CommandSpec(argv=[f"{settings.slurm_bin_dir}/slurmctld", "-t", "-f", "<tmp>"])
                )
            ],
            config_diff=repo.diff() if hasattr(repo, "diff") else None,
            blast_radius=_partition_blast_radius(inp),
            reversible=True,
        )

        event.diff_summary = f"partition {inp.op} {inp.name}"

        g = safety_gate.evaluate(
            meta,
            inp.model_dump(),
            diff,
            actor_role=actor_role,
            policy=policy,
            op=inp.op,
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

        # Commit config changes
        repo.stage("slurm/slurm.conf", new_conf)
        config_commit = repo.commit(f"Update partition {inp.name}")

        # Update partition model in state store
        with session_scope() as session:
            slurm_repo = SlurmRepo(session)
            partition = slurm_repo.get_partition(inp.name)
            if inp.op == "create" and partition is None:
                partition = Partition(name=inp.name)
                session.add(partition)

            if partition is not None:
                if inp.default is not None:
                    partition.is_default = inp.default
                if inp.max_time_min is not None:
                    partition.max_time_min = inp.max_time_min
                if inp.default_qos is not None:
                    partition.default_qos = inp.default_qos
            session.commit()

        # Reconfigure slurm controller
        res = run_command(
            CommandSpec(
                argv=[f"{settings.slurm_bin_dir}/scontrol", "reconfigure"],
                timeout_s=60,
            ),
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
                    message=f"scontrol reconfigure failed (rc={res.rc})",
                    detail=res.stderr,
                    remediation="check slurmctld status",
                )
            )

        event.decision = "auto"
        event.diff_summary = diff.render()
        event.result_status = "ok"
        audit.commit_event(event)

        return ToolResult.success(
            data={
                "partition": inp.name,
                "op": inp.op,
                "config_commit": config_commit,
            },
            diff=diff,
            audit_id=audit_id,
        )

    except _ToolBoundaryError as exc:
        event.result_status = "error"
        event.decision = "error"
        audit.commit_event(event)
        return ToolResult.failed(exc.error)


@tool(
    name="slurm.add_node_to_partition",
    risk=Risk.MEDIUM,
    domain="slurm",
    blast_radius=_partition_blast_radius,
)
def add_node_to_partition(
    inp: AddNodeToPartitionIn,
    *,
    actor: str,
    actor_role: Role = Role.OPERATOR,
    policy: PolicyEngine | None = None,
) -> ToolResult:
    """Add a node to a partition and configure its NodeName line in slurm.conf.

    This tool updates both the slurm.conf NodeName line for a node and
    adds it to the partition's Nodes list.
    """
    meta, _fn, _br = get_tool("slurm.add_node_to_partition")
    event = audit.new_event(
        actor=actor,
        tool=meta.name,
        risk=meta.risk.value,
        input=inp.model_dump(exclude={"dry_run"}),
    )
    audit_id = event.id

    try:
        from hpc_agent.state.configrepo import get_config_repo
        from hpc_agent.state.repos import NodeRepo

        repo = get_config_repo()

        # Read current slurm.conf
        current_conf = _read_slurm_conf("partition", actor, audit_id)

        # Get node info from state store
        from hpc_agent.state.db import session_scope
        from hpc_agent.state.models import Partition

        with session_scope() as session:
            node_repo = NodeRepo(session)
            node = node_repo.get(inp.node)
            if node is None:
                raise _precondition(f"node '{inp.node}' not found in state store")

            # Get partition
            partition = session.query(Partition).filter_by(name=inp.partition).first()
            if partition is None:
                raise _precondition(f"partition '{inp.partition}' does not exist")

            slurm_repo = SlurmRepo(session)

            # Build NodeName line with hw info
            node_line = f"NodeName={node.hostname}"
            if node.cpu_count:
                node_line += f" CPUs={node.cpu_count}"
            if node.mem_mb:
                node_line += f" RealMemory={node.mem_mb}"
            if node.gpu_count:
                gres = inp.gres or f"gpu:{node.gpu_model or 'gpu'}:{node.gpu_count}"
                node_line += f" Gres={gres}"
            if node.features:
                node_line += f" Features={node.features}"
            if inp.features:
                node_line += f" Features={','.join(inp.features)}"
            node_line += " State=UNKNOWN"

            # Read existing NodeName lines
            lines = current_conf.splitlines()
            # node_lines = [line for line in lines if line.startswith("NodeName=")]
            partition_lines = [line for line in lines if line.startswith("PartitionName=")]

            # Update or add NodeName line
            new_lines = []
            node_line_updated = False
            for line in lines:
                if line.startswith(f"NodeName={inp.node}"):
                    new_lines.append(node_line)
                    node_line_updated = True
                else:
                    new_lines.append(line)

            if not node_line_updated:
                new_lines.append(node_line)

            # Update partition's Nodes list
            updated_partition_lines = []
            for line in partition_lines:
                if line.startswith(f"PartitionName={inp.partition}"):
                    # Parse existing nodes and add our node
                    existing = _parse_partition_slurm_line(line)
                    partition_nodes = set(existing.get("nodes", []))
                    if inp.node not in partition_nodes:
                        partition_nodes.add(inp.node)
                        existing["nodes"] = sorted(partition_nodes)
                    new_line = _build_partition_line(inp.partition, existing)
                    updated_partition_lines.append(new_line)
                else:
                    updated_partition_lines.append(line)

            new_conf = "\n".join(updated_partition_lines)

            # Validate
            import os
            import tempfile

            with tempfile.NamedTemporaryFile(mode="w", suffix=".slurm.conf", delete=False) as tmp:
                tmp.write(new_conf)
                tmp_path = tmp.name

            try:
                res = run_command(
                    CommandSpec(
                        argv=[
                            f"{settings.slurm_bin_dir}/slurmctld",
                            "-t",
                            "-f",
                            tmp_path,
                        ],
                        timeout_s=30,
                    ),
                    actor=actor,
                    audit_id=audit_id,
                )
                if res.rc != 0:
                    raise _precondition(f"slurm.conf validation failed: {res.stderr}")
            finally:
                os.unlink(tmp_path)

            # Compute diff
            changes = [
                Change(
                    target=f"node/{inp.node}",
                    field=None,
                    before=None,
                    after=f"added to partition {inp.partition}",
                    op="add_to_partition",
                )
            ]

            diff = Diff(
                changes=changes,
                commands_preview=[
                    redacted_argv(
                        CommandSpec(
                            argv=[
                                f"{settings.slurm_bin_dir}/slurmctld",
                                "-t",
                                "-f",
                                "<tmp>",
                            ]
                        )
                    )
                ],
                config_diff=repo.diff() if hasattr(repo, "diff") else None,
                blast_radius=_partition_blast_radius(inp),
                reversible=True,
            )

            event.diff_summary = f"node {inp.node} -> partition {inp.partition}"

            g = safety_gate.evaluate(
                meta,
                inp.model_dump(),
                diff,
                actor_role=actor_role,
                policy=policy,
                op="add",
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

            # Commit config
            repo.stage("slurm/slurm.conf", new_conf)
            config_commit = repo.commit(f"Add {inp.node} to partition {inp.partition}")

            # Update partition members in state store
            slurm_repo.add_partition_member(inp.partition, inp.node)
            session.commit()

            # Reconfigure
            res = run_command(
                CommandSpec(
                    argv=[f"{settings.slurm_bin_dir}/scontrol", "reconfigure"],
                    timeout_s=60,
                ),
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
                        message=f"scontrol reconfigure failed (rc={res.rc})",
                        detail=res.stderr,
                        remediation="check slurmctld status",
                    )
                )

            event.decision = "auto"
            event.diff_summary = diff.render()
            event.result_status = "ok"
            audit.commit_event(event)

            return ToolResult.success(
                data={
                    "node": inp.node,
                    "partition": inp.partition,
                    "config_commit": config_commit,
                },
                diff=diff,
                audit_id=audit_id,
            )

    except _ToolBoundaryError as exc:
        event.result_status = "error"
        event.decision = "error"
        audit.commit_event(event)
        return ToolResult.failed(exc.error)
