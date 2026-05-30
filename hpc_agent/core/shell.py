"""Interactive operator shell.

This is a lightweight, dependency-free REPL for the agent. It keeps the familiar
one-shot Typer commands, but adds a conversational loop for planning, reviewing,
executing, and resuming approval-paused plans.
"""

from __future__ import annotations

from collections.abc import Callable

from hpc_agent.core.executor import resume_plan, run_plan
from hpc_agent.core.plan import Plan, PlanState, Step, StepStatus
from hpc_agent.core.planner import build_plan
from hpc_agent.exec.rbac import Role
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.tools.base import all_tools

Writer = Callable[[str], None]
Reader = Callable[[str], str]


HELP_TEXT = """Commands:
  <intent>              build and show a plan
  /run [intent]         execute the current plan, or build then execute intent
  /show                 show the current plan
  /approve [step]       approve and resume a paused plan step
  /tools                list registered tools
  /help                 show this help
  /exit                 leave the shell
"""


class ShellSession:
    """Stateful REPL session for one operator."""

    def __init__(
        self,
        *,
        actor: str,
        actor_role: Role,
        policy: PolicyEngine | None,
        write: Writer = print,
    ) -> None:
        self.actor = actor
        self.actor_role = actor_role
        self.policy = policy
        self.write = write
        self.current_plan: Plan | None = None
        self.running = True

    def handle_line(self, raw_line: str) -> None:
        """Handle one user-entered line."""
        line = raw_line.strip()
        if not line:
            return

        if line in {"/exit", "/quit", "exit", "quit"}:
            self.running = False
            self.write("bye")
            return
        if line in {"/help", "help", "?"}:
            self.write(HELP_TEXT.rstrip())
            return
        if line == "/tools":
            self._show_tools()
            return
        if line == "/show":
            self._show_current_plan()
            return
        if line.startswith("/approve"):
            self._approve(line.removeprefix("/approve").strip())
            return
        if line.startswith("/run"):
            self._run(line.removeprefix("/run").strip())
            return

        self._plan(line)

    def loop(self, *, read: Reader = input) -> None:
        """Run the interactive prompt until EOF or /exit."""
        self.write("hpc-agent shell. Type /help for commands, /exit to quit.")
        while self.running:
            try:
                line = read("hpc-agent> ")
            except EOFError:
                self.write("")
                break
            except KeyboardInterrupt:
                self.write("")
                continue
            self.handle_line(line)

    def _plan(self, intent: str) -> None:
        try:
            self.current_plan = build_plan(intent, actor=self.actor)
        except Exception as exc:  # noqa: BLE001 - REPL boundary
            self.write(f"Could not build a plan: {exc}")
            return
        self.write(self.render_plan(self.current_plan))
        self.write("Use /run to execute, or enter another intent to replace this plan.")

    def _run(self, intent: str) -> None:
        if intent:
            self._plan(intent)
            if self.current_plan is None:
                return
        if self.current_plan is None:
            self.write("No current plan. Enter an intent first.")
            return

        result = run_plan(
            self.current_plan,
            actor_role=self.actor_role,
            policy=self.policy,
        )
        self.current_plan = result
        self.write(self.render_plan(result, include_results=True))
        if result.state == PlanState.PAUSED:
            step = next((s for s in result.steps if s.status == StepStatus.NEEDS_APPROVAL), None)
            step_hint = f" {step.id}" if step else ""
            self.write(f"Plan paused for approval. Use /approve{step_hint} to resume.")

    def _approve(self, step_id: str) -> None:
        if self.current_plan is None:
            self.write("No current plan to approve.")
            return
        paused_steps = [s for s in self.current_plan.steps if s.status == StepStatus.NEEDS_APPROVAL]
        if not paused_steps:
            self.write("The current plan is not waiting for approval.")
            return
        target = step_id or paused_steps[0].id
        try:
            result = resume_plan(
                self.current_plan.id,
                target,
                approver=self.actor,
                actor_role=self.actor_role,
                policy=self.policy,
            )
        except Exception as exc:  # noqa: BLE001 - REPL boundary
            self.write(f"Approval failed: {exc}")
            return
        self.current_plan = result
        self.write(self.render_plan(result, include_results=True))

    def _show_current_plan(self) -> None:
        if self.current_plan is None:
            self.write("No current plan.")
            return
        self.write(self.render_plan(self.current_plan, include_results=True))

    def _show_tools(self) -> None:
        names = sorted(meta.name for meta in all_tools())
        self.write("Registered tools:")
        for name in names:
            self.write(f"  {name}")

    @staticmethod
    def render_plan(plan: Plan, *, include_results: bool = False) -> str:
        lines = [
            f"Plan {plan.id}",
            f"Intent: {plan.intent}",
            f"State: {plan.state.value}",
            "Steps:",
        ]
        for step in plan.steps:
            lines.extend(_render_step(step, include_results=include_results))
        return "\n".join(lines)


def _render_step(step: Step, *, include_results: bool) -> list[str]:
    dep = f" depends_on={step.depends_on}" if step.depends_on else ""
    lines = [
        f"  - {step.id}: {step.tool} status={step.status.value}{dep}",
        f"    input={step.input}",
    ]
    if include_results and step.result is not None:
        lines.append(f"    result={step.result.status.value}")
        if step.result.diff is not None:
            rendered = step.result.diff.render()
            lines.append("    diff:")
            lines.extend(f"      {line}" for line in rendered.splitlines())
        if step.result.error is not None:
            lines.append(f"    error={step.result.error.kind.value}: {step.result.error.message}")
    return lines
