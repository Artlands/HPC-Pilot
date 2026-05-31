"""CLI entry point. See spec 02 §7. Minimal scaffold — drives tools directly for now;
the planner/executor (spec 02) plugs in here later.
"""

from __future__ import annotations

import json

import typer

from hpc_agent.config.settings import settings
from hpc_agent.exec import audit
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
    BuildcacheIn,
    CreateViewIn,
    FindIn,
    GenModulesIn,
    InstallIn,
    ListEnvsIn,
    ManageCompilersIn,
    ManageEnvIn,
    SpecIn,
    create_view,
    find,
    generate_modules,
    install_packages,
    list_envs,
    manage_buildcache,
    manage_compilers,
    manage_environment,
    spec,
)

app = typer.Typer(help="HPC Pilot — AI agent for HPC cluster management")
QOS_LIST_OPTION = typer.Option(None)
QOS_ADD_OPTION = typer.Option(None)
RESERVATION_NODES_OPTION = typer.Option(None)
RESERVATION_USERS_OPTION = typer.Option(None)
RESERVATION_FLAGS_OPTION = typer.Option(None)
LIMIT_QOS_LIST_OPTION = typer.Option(None)
LIMIT_QOS_ADD_OPTION = typer.Option(None)
PLAYBOOK_ROLES_OPTION = typer.Option(None)


@app.callback()
def main() -> None:
    """Configure process-wide services for CLI commands."""
    audit.configure_from_settings()


@app.command()
def tools() -> None:
    """List registered tools and their JSON schemas."""
    typer.echo(json.dumps(tool_schemas(), indent=2))


@app.command("audit-init")
def audit_init_cmd() -> None:
    """Create the durable audit operation-log tables."""
    audit.init_audit_db(settings.audit_db_url)
    typer.echo(f"initialized audit DB: {settings.audit_db_url}")


@app.command("audit-log")
def audit_log_cmd(
    limit: int = typer.Option(20, help="Maximum events to show."),
    tool: str = typer.Option(None, help="Filter by tool name."),
    actor: str = typer.Option(None, help="Filter by actor."),
    result_status: str = typer.Option(None, help="Filter by result status, e.g. ok."),
    json_output: bool = typer.Option(False, "--json", help="Render full JSON events."),
) -> None:
    """List tracked audit operations."""
    events = audit.list_events(
        limit=limit,
        tool=tool,
        actor=actor,
        result_status=result_status,
    )
    if json_output:
        typer.echo(json.dumps([event.model_dump(mode="json") for event in events], indent=2))
        return
    for event in events:
        command_count = len(event.commands)
        typer.echo(
            f"{event.ts.isoformat()} {event.id} {event.result_status or '-'} "
            f"{event.actor} {event.tool} decision={event.decision} commands={command_count}"
        )


@app.command("audit-show")
def audit_show_cmd(audit_id: str) -> None:
    """Show one tracked audit operation."""
    event = audit.get_event(audit_id)
    if event is None:
        raise typer.BadParameter(f"unknown audit id: {audit_id}")
    typer.echo(event.model_dump_json(indent=2))


@app.command("shell")
def shell_cmd(
    actor: str = typer.Option("cli-user", help="Operator identity for audit records."),
    role: str = typer.Option("operator", help="RBAC role: viewer, operator, admin."),
) -> None:
    """Start an interactive agent shell."""
    from hpc_agent.core.shell import ShellSession

    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    ShellSession(actor=actor, actor_role=Role(role), policy=policy).loop()


@app.command("tui")
def tui_cmd(
    actor: str = typer.Option("cli-user", help="Operator identity for audit records."),
    role: str = typer.Option("operator", help="RBAC role: viewer, operator, admin."),
) -> None:
    """Start the conversational TUI."""
    from hpc_agent.core.shell import ShellSession
    from hpc_agent.core.tui import HPCPilotApp

    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    session = ShellSession(actor=actor, actor_role=Role(role), policy=policy)
    HPCPilotApp(session).run()


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


@app.command("spack-compilers")
def spack_compilers_cmd(
    op: str = typer.Option("find", help="Operation: find or add"),
    scope: str = typer.Option("site", help="Scope: site or env"),
    path: str = typer.Option(None, help="Compiler path for add operation"),
    env: str = typer.Option(None, help="Environment name (for env scope)"),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Find or add compilers to Spack (dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = ManageCompilersIn(op=op, scope=scope, path=path, env=env, dry_run=not apply)
    result = manage_compilers(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-env")
def spack_env_cmd(
    name: str = typer.Argument(..., help="Environment name"),
    op: str = typer.Option("create", help="Operation: create, add_specs, remove_specs"),
    specs: list[str] | None = None,
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Create or modify a Spack environment (dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = ManageEnvIn(name=name, op=op, specs=specs or [], dry_run=not apply)
    result = manage_environment(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-buildcache")
def spack_buildcache_cmd(
    op: str = typer.Argument(..., help="Operation: push, update_index, add_mirror"),
    mirror: str = typer.Argument(..., help="Mirror path or URL"),
    env: str = typer.Option(None, help="Spack environment (for push)"),
    signing_key_ref: str = typer.Option(None, help="GPG key reference"),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Manage Spack buildcache (push/update/add_mirror, dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = BuildcacheIn(
        op=op, mirror=mirror, env=env, signing_key_ref=signing_key_ref, dry_run=not apply
    )
    result = manage_buildcache(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-modules")
def spack_modules_cmd(
    env: str = typer.Argument(..., help="Spack environment name"),
    module_type: str = typer.Option("lmod", help="Module type: lmod or tcl"),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Generate Lmod/Tcl modulefiles for a Spack environment (dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = GenModulesIn(env=env, module_type=module_type, dry_run=not apply)
    result = generate_modules(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-view")
def spack_view_cmd(
    env: str = typer.Argument(..., help="Spack environment name"),
    prefix: str = typer.Option(None, help="View prefix path (default: env default)"),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Create/enable a filesystem view for a Spack environment (dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = CreateViewIn(env=env, prefix=prefix, dry_run=not apply)
    result = create_view(inp, actor="cli-user", actor_role=Role(role), policy=policy)
    typer.echo(result.model_dump_json(indent=2))


@app.command("spack-install")
def spack_install_cmd(
    env: str = typer.Argument(..., help="Spack environment name"),
    use_buildcache: bool = typer.Option(True, help="Use buildcache if available"),
    jobs: int = typer.Option(16, help="Number of parallel jobs"),
    apply: bool = typer.Option(False, help="Actually apply (default is dry-run)."),
    role: str = typer.Option("operator"),
) -> None:
    """Install Spack packages in an environment (dry-run unless --apply)."""
    policy = PolicyEngine.from_dir(f"{settings.config_repo}/policy")
    inp = InstallIn(env=env, use_buildcache=use_buildcache, jobs=jobs, dry_run=not apply)
    result = install_packages(inp, actor="cli-user", actor_role=Role(role), policy=policy)
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
