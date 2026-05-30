"""CLI entry point. See spec 02 §7. Minimal scaffold — drives tools directly for now;
the planner/executor (spec 02) plugs in here later.
"""

from __future__ import annotations

import json

import typer

from hpc_agent.config.settings import settings
from hpc_agent.exec.rbac import Role
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.base import tool_schemas
from hpc_agent.tools.slurm import ManageQOSIn, manage_qos

app = typer.Typer(help="HPC management agent")


@app.command()
def tools() -> None:
    """List registered tools and their JSON schemas."""
    typer.echo(json.dumps(tool_schemas(), indent=2))


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
