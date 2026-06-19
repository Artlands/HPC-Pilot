"""Skills/runbooks runner for HPC Pilot.

Skills are YAML files that describe multi-step HPC procedures.  Each skill
defines required inputs, a list of steps (each calling an HPC tool or a
built-in), and failure-handling behavior.

Skill YAML schema example::

    name: drain-and-patch-gpu-node
    description: Safely drain a GPU node, patch, reboot, verify, resume.
    required_role: admin
    inputs:
      - name: node
        type: string
        required: true
      - name: reason
        type: string
        default: "scheduled-patch"
    steps:
      - id: snapshot_state
        tool: hpc_slurm_node_status
        args: {node: "{{ node }}"}
      - id: drain
        tool: hpc_slurm_node_state
        args: {node: "{{ node }}", target: drain, reason: "{{ reason }}"}
        approval: required
    on_failure: pause

Run records are persisted to ~/.hpc-pilot/skills/runs/<id>.json.
"""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from hpc_pilot.paths import ensure_layout, skill_runs_dir, skills_dir

# ---------------------------------------------------------------------------
# Skill YAML model
# ---------------------------------------------------------------------------

@dataclass
class SkillInput:
    name: str
    type: str = "string"
    required: bool = False
    default: Any = None


@dataclass
class SkillStep:
    id: str
    tool: str = ""
    builtin: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    approval: str = ""  # "required" | ""


@dataclass
class Skill:
    name: str
    description: str = ""
    required_role: str = "operator"
    inputs: list[SkillInput] = field(default_factory=list)
    steps: list[SkillStep] = field(default_factory=list)
    on_failure: str = "abort"  # "pause" | "abort"


def _parse_skill(data: dict[str, Any]) -> Skill:
    inputs = [
        SkillInput(
            name=str(inp["name"]),
            type=str(inp.get("type", "string")),
            required=bool(inp.get("required", False)),
            default=inp.get("default"),
        )
        for inp in (data.get("inputs") or [])
    ]
    steps = [
        SkillStep(
            id=str(s.get("id", f"step_{i}")),
            tool=str(s.get("tool", "")),
            builtin=str(s.get("builtin", "")),
            args=dict(s.get("args") or {}),
            approval=str(s.get("approval", "")),
        )
        for i, s in enumerate(data.get("steps") or [])
    ]
    return Skill(
        name=str(data.get("name", "")),
        description=str(data.get("description", "")),
        required_role=str(data.get("required_role", "operator")),
        inputs=inputs,
        steps=steps,
        on_failure=str(data.get("on_failure", "abort")),
    )


# ---------------------------------------------------------------------------
# Template rendering  ({{ var }} → value)
# ---------------------------------------------------------------------------

_TMPL_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _TMPL_RE.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), value)
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, ctx) for v in value]
    return value


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    step_id: str
    status: str  # "ok" | "error" | "skipped" | "pending_approval"
    output: str = ""
    error: str = ""
    duration_ms: int = 0


@dataclass
class SkillRun:
    run_id: str
    skill_name: str
    actor: str
    role: str
    inputs: dict[str, Any]
    cluster: str
    started_at: float
    status: str = "running"  # "running" | "paused" | "completed" | "failed"
    paused_at_step: int = 0
    step_results: list[StepResult] = field(default_factory=list)
    finished_at: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillRun:
        d = dict(d)
        d["step_results"] = [StepResult(**s) for s in d.get("step_results", [])]
        return cls(**d)


def _save_run(run: SkillRun) -> None:
    ensure_layout()
    path = os.path.join(skill_runs_dir(), f"{run.run_id}.json")
    with open(path, "w") as f:
        json.dump(run.to_dict(), f, indent=2, default=str)


def _load_run(run_id: str) -> SkillRun:
    path = os.path.join(skill_runs_dir(), f"{run_id}.json")
    with open(path) as f:
        return SkillRun.from_dict(json.load(f))


# ---------------------------------------------------------------------------
# Skill discovery
# ---------------------------------------------------------------------------

_BUILTIN_DIR = os.path.join(os.path.dirname(__file__), "builtin")


def _skill_dirs() -> list[str]:
    dirs = []
    if os.path.isdir(_BUILTIN_DIR):
        dirs.append(_BUILTIN_DIR)
    user_dir = skills_dir()
    if os.path.isdir(user_dir):
        dirs.append(user_dir)
    return dirs


def load_skill(name: str) -> Skill:
    """Load a skill by name from built-in or user skill directories.

    Raises FileNotFoundError when the skill is not found.
    """
    import yaml

    for d in _skill_dirs():
        path = os.path.join(d, f"{name}.yaml")
        if os.path.exists(path):
            with open(path) as f:
                return _parse_skill(yaml.safe_load(f))
    raise FileNotFoundError(f"Skill not found: {name!r}")


def list_skills() -> list[dict[str, str]]:
    """Return a list of {name, description, required_role} for all known skills."""
    import yaml

    seen: set[str] = set()
    result: list[dict[str, str]] = []
    for d in _skill_dirs():
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".yaml"):
                continue
            skill_name = fname[:-5]
            if skill_name in seen:
                continue
            seen.add(skill_name)
            try:
                with open(os.path.join(d, fname)) as f:
                    data = yaml.safe_load(f) or {}
                result.append({
                    "name": str(data.get("name", skill_name)),
                    "description": str(data.get("description", "")),
                    "required_role": str(data.get("required_role", "operator")),
                })
            except Exception:
                continue
    return result


# ---------------------------------------------------------------------------
# Skill runner
# ---------------------------------------------------------------------------

class SkillRunner:
    """Execute a skill step-by-step, persisting state after each step."""

    def __init__(self, cluster: str = "default") -> None:
        self.cluster = cluster

    def run(
        self,
        name: str,
        inputs: dict[str, Any],
        *,
        role: Any,  # hpc_pilot.rbac.Role
        actor: str,
        resume_run_id: str | None = None,
    ) -> SkillRun:
        """Execute skill *name* with *inputs*, enforcing *role*.

        When *resume_run_id* is provided, continue a paused run from the saved
        state rather than starting fresh.
        """
        from hpc_pilot.rbac import Role

        skill = load_skill(name)

        # RBAC: skill-level gate before any step runs
        required = Role(skill.required_role)
        if not (role >= required):
            raise PermissionError(
                f"Skill '{name}' requires role '{skill.required_role}'; "
                f"current role is '{role.value}'"
            )

        if resume_run_id:
            run = _load_run(resume_run_id)
            start_step = run.paused_at_step
        else:
            # Resolve input defaults and validate required inputs
            resolved: dict[str, Any] = {}
            for inp in skill.inputs:
                if inp.name in inputs:
                    resolved[inp.name] = inputs[inp.name]
                elif inp.default is not None:
                    resolved[inp.name] = inp.default
                elif inp.required:
                    raise ValueError(f"Skill '{name}': missing required input '{inp.name}'")
            run = SkillRun(
                run_id=str(uuid.uuid4()),
                skill_name=name,
                actor=actor,
                role=role.value,
                inputs=resolved,
                cluster=self.cluster,
                started_at=time.time(),
            )
            start_step = 0

        ctx = dict(run.inputs)
        _save_run(run)

        for i, step in enumerate(skill.steps[start_step:], start=start_step):
            t0 = time.monotonic()
            step_result = StepResult(step_id=step.id, status="running")

            if step.approval == "required":
                step_result.status = "pending_approval"
                step_result.output = f"Waiting for approval before step '{step.id}'"
                run.step_results.append(step_result)
                run.status = "paused"
                run.paused_at_step = i
                _save_run(run)
                return run

            try:
                output = self._execute_step(step, ctx, role=role, actor=actor)
                step_result.status = "ok"
                step_result.output = output
                ctx[step.id] = output
            except Exception as exc:
                step_result.status = "error"
                step_result.error = str(exc)
                step_result.duration_ms = int((time.monotonic() - t0) * 1000)
                run.step_results.append(step_result)
                if skill.on_failure == "pause":
                    run.status = "paused"
                    run.paused_at_step = i
                else:
                    run.status = "failed"
                    run.error = str(exc)
                    run.finished_at = time.time()
                _save_run(run)
                return run

            step_result.duration_ms = int((time.monotonic() - t0) * 1000)
            run.step_results.append(step_result)
            _save_run(run)

        run.status = "completed"
        run.finished_at = time.time()
        _save_run(run)
        return run

    def _execute_step(
        self,
        step: SkillStep,
        ctx: dict[str, Any],
        *,
        role: Any,
        actor: str,
    ) -> str:
        from hpc_pilot.dispatch import invoke

        rendered_args = _render(step.args, ctx)
        rendered_args["cluster"] = rendered_args.get("cluster", self.cluster)

        if step.tool:
            return invoke(step.tool, rendered_args, role=role, actor=actor)
        if step.builtin == "wait_until":
            return "(builtin wait_until not yet implemented; skipping)"
        if step.builtin == "for_each":
            return "(builtin for_each not yet implemented; skipping)"
        raise ValueError(f"Step '{step.id}' has neither 'tool' nor recognized 'builtin'")


# ---------------------------------------------------------------------------
# Agent-facing tools
# ---------------------------------------------------------------------------

def hpc_skill_describe(name: str) -> str:
    """Return the YAML content of a named skill."""
    import yaml

    skill = load_skill(name)
    # Re-serialize to YAML for LLM readability
    data = {
        "name": skill.name,
        "description": skill.description,
        "required_role": skill.required_role,
        "inputs": [
            {"name": i.name, "type": i.type, "required": i.required, "default": i.default}
            for i in skill.inputs
        ],
        "steps": [
            {k: v for k, v in {"id": s.id, "tool": s.tool or None, "builtin": s.builtin or None,
                                "args": s.args or None, "approval": s.approval or None}.items()
             if v is not None}
            for s in skill.steps
        ],
        "on_failure": skill.on_failure,
    }
    return yaml.dump(data, default_flow_style=False)


def hpc_skill_run(
    name: str,
    inputs: dict[str, Any] | None = None,
    *,
    role: Any,
    actor: str,
    cluster: str = "default",
    resume_run_id: str | None = None,
) -> dict[str, Any]:
    """Execute a named skill and return the run record as a dict."""
    runner = SkillRunner(cluster=cluster)
    run = runner.run(
        name,
        inputs or {},
        role=role,
        actor=actor,
        resume_run_id=resume_run_id,
    )
    return {
        "run_id": run.run_id,
        "skill": run.skill_name,
        "status": run.status,
        "steps": [
            {"id": s.step_id, "status": s.status, "duration_ms": s.duration_ms,
             "output": s.output[:200] if s.output else "", "error": s.error}
            for s in run.step_results
        ],
        "error": run.error,
        "paused_at_step": run.paused_at_step if run.status == "paused" else None,
    }
