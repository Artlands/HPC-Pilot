"""CLI entry point. See spec 02 §7. Minimal scaffold — drives tools directly for now;
the planner/executor (spec 02) plugs in here later.
"""

from __future__ import annotations

import json

import typer

from hpc_agent.config.settings import settings
from hpc_agent.exec.rbac import Role
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.ansible import (
    ComposePlaybookIn,
    LintPlaybookIn,
    ManageInventoryIn,
    ManageSecretIn,
    RunPlaybookIn,
    compose_playbook,
    lint_playbook,
    manage_inventory,
    manage_secret,
    run_playbook,
)
from hpc_agent.tools.base import tool_schemas
from hpc_agent.tools.slurm import (
    DiagIn,
    ExtendAccountIn,
    JobAccountingIn,
    ManageQOSIn,
    ManageReservationIn,
    ManageUserAssocIn,
    NodeStateIn,
    NodeStatusIn,
    QueueIn,
    ReconfigureIn,
    SetLimitsIn,
    ShowAssocIn,
    UsageReportIn,
    diag,
    extend_account,
    job_accounting,
    manage_qos,
    manage_reservation,
    manage_user_assoc,
    node_state,
    node_status,
    queue,
    reconfigure,
    set_limits,
    show_assoc,
    usage_report,
)
from hpc_agent.tools.spack import (
    FindIn,
    ListEnvsIn,
    SpecIn,
    find,
    list_envs,
    spec,
)

app = typer.Typer(help="HPC management agent")
QOS_LIST_OPTION = typer.Option(None)
QOS_ADD_OPTION = typer.Option(None)
RESERVATION_NODES_OPTION = typer.Option(None)
RESERVATION_USERS_OPTION = typer.Option(None)
RESERVATION_FLAGS_OPTION = typer.Option(None)
LIMIT_QOS_LIST_OPTION = typer.Option(None)
LIMIT_QOS_ADD_OPTION = typer.Option(None)
PLAYBOOK_ROLES_OPTION = typer.Option(None)


@app.command()
def tools() -> None:
    """List registered tools and their JSON schemas."""
    typer.echo(json.dumps(tool_schemas(), indent=2))


@app.command("lint-playbook")
def lint_playbook_cmd(
    playbook: str,
    role: str = typer.Option("operator"),
) -> None:
    """Run ansible-lint and Ansible syntax checks for a playbook."""
    result = lint_playbook(
        LintPlaybookIn(playbook=playbook),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("compose-playbook")
def compose_playbook_cmd(
    name: str,
    target_group: str,
    roles: list[str] | None = PLAYBOOK_ROLES_OPTION,
    apply: bool = typer.Option(False, help="Actually write the playbook (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Render a playbook from curated roles."""
    result = compose_playbook(
        ComposePlaybookIn(
            name=name,
            target_group=target_group,
            roles=roles or [],
            dry_run=not apply,
        ),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("manage-inventory")
def manage_inventory_cmd(
    apply: bool = typer.Option(False, help="Actually write inventory (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Generate Ansible inventory from state-store nodes."""
    result = manage_inventory(
        ManageInventoryIn(dry_run=not apply),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("run-playbook")
def run_playbook_cmd(
    playbook: str,
    limit: str = typer.Option(None),
    apply: bool = typer.Option(False, help="Actually apply (default is check/diff dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Run an Ansible playbook through lint and check/diff first."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    result = run_playbook(
        RunPlaybookIn(playbook=playbook, limit=limit, dry_run=not apply),
        actor="cli-user",
        actor_role=Role(role),
        policy=policy,
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("check-secret")
def check_secret_cmd(
    ref: str,
    role: str = typer.Option("operator"),
) -> None:
    """Check that a secret reference exists without printing secret material."""
    result = manage_secret(
        ManageSecretIn(ref=ref),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-envs")
def spack_envs_cmd(role: str = typer.Option("viewer")) -> None:
    """List configured Spack environments."""
    result = list_envs(ListEnvsIn(), actor="cli-user", actor_role=Role(role))
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-find")
def spack_find_cmd(env: str, role: str = typer.Option("viewer")) -> None:
    """List installed specs in a Spack environment."""
    result = find(FindIn(env=env), actor="cli-user", actor_role=Role(role))
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-spec")
def spack_spec_cmd(spec_text: str, role: str = typer.Option("viewer")) -> None:
    """Preview concretization for a Spack spec."""
    result = spec(SpecIn(spec=spec_text), actor="cli-user", actor_role=Role(role))
    typer.echo(result.model_dump_json(indent=2))


@app.command("qos")
def qos(
    name: str,
    op: str = typer.Option("modify"),
    max_wall_min: int = typer.Option(None),
    max_tres: str = typer.Option(None),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Create/modify a QOS (dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = ManageQOSIn(
        name=name, op=op, max_wall_min=max_wall_min, max_tres=max_tres, dry_run=not apply
    )
    result = manage_qos(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("account")
def account(
    name: str,
    op: str = typer.Option("modify"),
    parent: str = typer.Option(None),
    organization: str = typer.Option(None),
    description: str = typer.Option(None),
    grp_tres: str = typer.Option(None),
    max_wall_min: int = typer.Option(None),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Create/modify a Slurm account (dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = ExtendAccountIn(
        name=name,
        op=op,
        parent=parent,
        organization=organization,
        description=description,
        grp_tres=grp_tres,
        max_wall_min=max_wall_min,
        dry_run=not apply,
    )
    result = extend_account(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("assoc")
def assoc(
    user: str,
    account: str,
    op: str = typer.Option("modify"),
    qos_list: list[str] | None = QOS_LIST_OPTION,
    qos_add: list[str] | None = QOS_ADD_OPTION,
    default_qos: str = typer.Option(None),
    fairshare: int = typer.Option(None),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Create/modify a Slurm user/account association."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = ManageUserAssocIn(
        user=user,
        account=account,
        op=op,
        qos_list=qos_list,
        qos_add=qos_add,
        default_qos=default_qos,
        fairshare=fairshare,
        dry_run=not apply,
    )
    result = manage_user_assoc(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("set-limits")
def set_limits_cmd(
    target: str,
    name: str = typer.Option(None),
    user: str = typer.Option(None),
    account: str = typer.Option(None),
    max_wall_min: int = typer.Option(None),
    grp_tres: str = typer.Option(None),
    max_tres: str = typer.Option(None),
    max_jobs_pu: int = typer.Option(None),
    qos_list: list[str] | None = LIMIT_QOS_LIST_OPTION,
    qos_add: list[str] | None = LIMIT_QOS_ADD_OPTION,
    default_qos: str = typer.Option(None),
    fairshare: int = typer.Option(None),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Set limits on an account, QOS, or user/account association."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = SetLimitsIn(
        target=target,
        name=name,
        user=user,
        account=account,
        max_wall_min=max_wall_min,
        grp_tres=grp_tres,
        max_tres=max_tres,
        max_jobs_pu=max_jobs_pu,
        qos_list=qos_list,
        qos_add=qos_add,
        default_qos=default_qos,
        fairshare=fairshare,
        dry_run=not apply,
    )
    result = set_limits(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("show-assoc")
def show_assoc_cmd(
    user: str = typer.Option(None),
    account: str = typer.Option(None),
    role: str = typer.Option("viewer"),
) -> None:
    """Show Slurm associations."""
    result = show_assoc(
        ShowAssocIn(user=user, account=account), actor="cli-user", actor_role=Role(role)
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("node-status")
def node_status_cmd(
    node: str = typer.Option(None),
    role: str = typer.Option("viewer"),
) -> None:
    """Show Slurm node status."""
    result = node_status(
        NodeStatusIn(node=node),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("queue")
def queue_cmd(
    user: str = typer.Option(None),
    partition: str = typer.Option(None),
    role: str = typer.Option("viewer"),
) -> None:
    """Show Slurm queue jobs."""
    result = queue(
        QueueIn(user=user, partition=partition),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("job-accounting")
def job_accounting_cmd(
    user: str = typer.Option(None),
    account: str = typer.Option(None),
    start: str = typer.Option(None),
    end: str = typer.Option(None),
    state: str = typer.Option(None),
    role: str = typer.Option("viewer"),
) -> None:
    """Show completed Slurm job accounting records."""
    result = job_accounting(
        JobAccountingIn(user=user, account=account, start=start, end=end, state=state),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("usage-report")
def usage_report_cmd(
    start: str = typer.Option(None),
    end: str = typer.Option(None),
    account: str = typer.Option(None),
    user: str = typer.Option(None),
    role: str = typer.Option("viewer"),
) -> None:
    """Show Slurm user/account utilization."""
    result = usage_report(
        UsageReportIn(start=start, end=end, account=account, user=user),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("diag")
def diag_cmd(
    include_config: bool = typer.Option(True),
    include_sdiag: bool = typer.Option(True),
    role: str = typer.Option("viewer"),
) -> None:
    """Show Slurm controller diagnostics."""
    result = diag(
        DiagIn(include_config=include_config, include_sdiag=include_sdiag),
        actor="cli-user",
        actor_role=Role(role),
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("node-state")
def node_state_cmd(
    node: str,
    target: str,
    reason: str = typer.Option(None),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Drain, resume, down, or undrain a Slurm node."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = NodeStateIn(node=node, target=target, reason=reason, dry_run=not apply)
    result = node_state(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("reconfigure")
def reconfigure_cmd(
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Ask the Slurm controller to re-read its configuration."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    result = reconfigure(
        ReconfigureIn(dry_run=not apply),
        actor="cli-user",
        actor_role=Role(role),
        policy=policy,
    )
    typer.echo(result.model_dump_json(indent=2))


@app.command("reservation")
def reservation_cmd(
    name: str,
    op: str,
    nodes: list[str] | None = RESERVATION_NODES_OPTION,
    start: str = typer.Option(None),
    duration_min: int = typer.Option(None),
    users: list[str] | None = RESERVATION_USERS_OPTION,
    flags: list[str] | None = RESERVATION_FLAGS_OPTION,
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Create or delete a Slurm maintenance reservation."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = ManageReservationIn(
        name=name,
        op=op,
        nodes=nodes,
        start=start,
        duration_min=duration_min,
        users=users,
        flags=flags if flags is not None else ["MAINT", "IGNORE_JOBS"],
        dry_run=not apply,
    )
    result = manage_reservation(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("plan")
def plan(
    intent: str,
    apply: bool = typer.Option(False, help="Execute the plan (default just shows it)."),
    role: str = typer.Option("operator"),
) -> None:
    """Build a plan from a natural-language intent and optionally execute it."""
    from hpc_agent.core.executor import run_plan
    from hpc_agent.core.planner import build_plan

    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    p = build_plan(intent, actor="cli-user")
    typer.echo(f"Plan {p.id} — {len(p.steps)} step(s):")
    for s in p.steps:
        typer.echo(f"  [{s.id}] {s.tool} {s.input}")
    if not apply:
        typer.echo("(dry preview only; pass --apply to execute)")
        return
    result = run_plan(p, actor_role=Role(role), policy=policy)
    typer.echo(f"Plan state: {result.state.value}")
    for s in result.steps:
        status = s.result.status.value if s.result else s.status.value
        typer.echo(f"  [{s.id}] {s.status.value} -> {status}")


if __name__ == "__main__":
    app()
