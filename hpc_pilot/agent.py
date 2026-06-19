"""
HPC Pilot AI agent — Claude-powered tool-use loop for cluster management.

The agent maps every hpc_* tool function to an Anthropic tool schema and runs
the standard tool-use loop:  user → Claude → tool call → result → Claude → answer.

Usage (programmatic):
    from hpc_pilot.agent import HpcAgent
    agent = HpcAgent()
    text, history = agent.run_turn("Show cluster health", history=[])

Usage (streaming CLI):
    text, history = agent.run_turn(
        "Drain gpu01 for maintenance",
        history=[],
        on_text=lambda chunk: print(chunk, end="", flush=True),
        on_tool=lambda name, args: print(f"\n  [→ {name}]", flush=True),
    )
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from hpc_pilot.audit import audit_tool
from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role, check_permission, get_role

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "hpc_slurm_node_status",
        "description": (
            "Show detailed Slurm node status (CPU, memory, state, running jobs). "
            "Leave node empty to show all nodes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {
                    "type": "string",
                    "description": "Node name. Empty string = show all nodes.",
                }
            },
        },
    },
    {
        "name": "hpc_slurm_queue",
        "description": "Show the Slurm job queue, optionally filtered by user, partition or state.",
        "input_schema": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "Filter by username"},
                "partition": {"type": "string", "description": "Filter by partition name"},
                "state": {
                    "type": "string",
                    "description": "Filter by job state, e.g. RUNNING, PENDING",
                },
            },
        },
    },
    {
        "name": "hpc_slurm_node_state",
        "description": (
            "Change a Slurm node's state. "
            "drain = prevent new jobs; undrain/resume = allow jobs again; down = mark failed. "
            "Always query current state before changing it. "
            "Use dry_run=true to preview without executing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "target": {
                    "type": "string",
                    "enum": ["drain", "undrain", "resume", "down"],
                },
                "reason": {"type": "string", "description": "Reason for the state change"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview the command without executing (default: true)",
                },
            },
            "required": ["node", "target"],
        },
    },
    {
        "name": "hpc_slurm_qos_modify",
        "description": (
            "Modify a Slurm QOS (Quality of Service) setting. "
            "Use dry_run=true first to preview the sacctmgr command before applying."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "QOS name"},
                "max_wall_min": {
                    "type": "integer",
                    "description": "Maximum wall-clock time in minutes",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without executing (default: true)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_warewulf_node_status",
        "description": "List Warewulf-provisioned nodes with their assigned boot images.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_warewulf_image_list",
        "description": "List available Warewulf container images.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_warewulf_bootstrap",
        "description": (
            "Bootstrap (PXE-boot) a Warewulf node. This is destructive — "
            "use dry_run=true to preview first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview without executing (default: true)",
                },
            },
            "required": ["node"],
        },
    },
    {
        "name": "hpc_spack_env_list",
        "description": "List all Spack environments on the cluster.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_spack_find",
        "description": "List installed software packages inside a Spack environment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "env": {"type": "string", "description": "Spack environment name"}
            },
            "required": ["env"],
        },
    },
    {
        "name": "hpc_spack_compilers",
        "description": "List available compilers registered in Spack.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_ansible_playbook_run",
        "description": (
            "Run an Ansible playbook against cluster nodes. "
            "Pass check=true to do a Ansible dry-run (--check). "
            "Pass dry_run=true to preview the ansible-playbook command without executing at all."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "playbook": {
                    "type": "string",
                    "description": "Absolute path to the YAML playbook file",
                },
                "limit": {
                    "type": "string",
                    "description": "Ansible host limit pattern (e.g. 'gpu_nodes')",
                },
                "check": {
                    "type": "boolean",
                    "description": "Pass --check to ansible-playbook (no changes on hosts)",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "Preview command without executing (default: true)",
                },
            },
            "required": ["playbook"],
        },
    },
    {
        "name": "hpc_ansible_inventory_generate",
        "description": "Generate and display the current Ansible inventory.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "hpc_cluster_health_check",
        "description": (
            "Run a comprehensive health check across all installed cluster components "
            "(Slurm, Warewulf, Spack, Ansible). Reports status and any detected issues."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are HPC Pilot, an AI assistant for managing HPC clusters.
You have tools for Slurm, Warewulf, Ansible, and Spack.

Operator : {actor}
Role     : {role}

Role permissions:
• viewer   — read-only queries (node status, queue, health, Spack, Warewulf images)
• operator — viewer + drain/resume nodes
• admin    — operator + modify QOS, run Ansible playbooks, bootstrap Warewulf nodes

Interaction guidelines:
1. When asked about cluster state, call the relevant tool immediately.
2. Before any mutating operation, first query the current state to explain what will change.
3. For mutations, start with dry_run=true to show the command; only set dry_run=false
   after the operator explicitly confirms.
4. If a tool raises a permission error, explain what role is required.
5. Format output as Markdown: tables for tabular data, code blocks for raw command output.
6. Be concise — administrators are busy.
"""

# ---------------------------------------------------------------------------
# HpcAgent
# ---------------------------------------------------------------------------


def _load_env() -> None:
    """Load ~/.hpc-pilot/.env into the environment (silent if dotenv not installed)."""
    try:
        from dotenv import load_dotenv  # type: ignore[import]

        env_file = os.path.join(get_home(), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file, override=False)
    except ImportError:
        pass


class HpcAgent:
    """Claude-powered agent that drives HPC cluster tool calls."""

    def __init__(
        self,
        model: str = "claude-opus-4-7",
        role: Role | None = None,
        actor: str | None = None,
    ) -> None:
        _load_env()
        from anthropic import Anthropic  # imported lazily so tests can stub

        self.model = model
        self.role: Role = role if role is not None else get_role()
        self.actor: str = actor or os.environ.get("HPC_PILOT_ACTOR", "agent")
        self._client = Anthropic()

    # ------------------------------------------------------------------
    # System prompt (with prompt-caching header)
    # ------------------------------------------------------------------

    def _system_prompt_blocks(self) -> list[dict[str, Any]]:
        text = _SYSTEM_PROMPT.format(actor=self.actor, role=self.role.value)
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Map Anthropic tool name + args to the real tool function."""
        from hpc_pilot import tools  # deferred to avoid circular imports

        if name == "hpc_slurm_node_status":
            return tools.hpc_slurm_node_status(args.get("node", ""))

        if name == "hpc_slurm_queue":
            filters = {k: v for k, v in args.items() if k in ("user", "partition", "state") and v}
            return tools.hpc_slurm_queue(filters or None)

        if name == "hpc_slurm_node_state":
            return tools.hpc_slurm_node_state(
                args["node"],
                args["target"],
                args.get("reason") or None,
                bool(args.get("dry_run", True)),
            )

        if name == "hpc_slurm_qos_modify":
            return tools.hpc_slurm_qos_modify(
                args["name"],
                args.get("max_wall_min"),
                bool(args.get("dry_run", True)),
            )

        if name == "hpc_warewulf_node_status":
            return tools.hpc_warewulf_node_status()

        if name == "hpc_warewulf_image_list":
            return tools.hpc_warewulf_image_list()

        if name == "hpc_warewulf_bootstrap":
            return tools.hpc_warewulf_bootstrap(args["node"], bool(args.get("dry_run", True)))

        if name == "hpc_spack_env_list":
            return tools.hpc_spack_env_list()

        if name == "hpc_spack_find":
            return tools.hpc_spack_find(args["env"])

        if name == "hpc_spack_compilers":
            return tools.hpc_spack_compilers()

        if name == "hpc_ansible_playbook_run":
            return tools.hpc_ansible_playbook_run(
                args["playbook"],
                args.get("limit") or None,
                bool(args.get("check", False)),
                bool(args.get("dry_run", True)),
            )

        if name == "hpc_ansible_inventory_generate":
            return tools.hpc_ansible_inventory_generate()

        if name == "hpc_cluster_health_check":
            result = tools.hpc_cluster_health_check()
            return json.dumps(result, indent=2, default=str)

        return f"[unknown tool: {name}]"

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute one tool call: RBAC-check → audit → dispatch → return string result."""
        dry_run = bool(args.get("dry_run", False))
        check_permission(name, self.role)
        try:
            with audit_tool(name, self.actor, self.role.value, args, dry_run=dry_run):
                return self._call_tool(name, args) or "(no output)"
        except RuntimeError as exc:
            return f"[Tool error] {exc}"
        except ValueError as exc:
            return f"[Input error] {exc}"

    # ------------------------------------------------------------------
    # Conversation turn
    # ------------------------------------------------------------------

    def run_turn(
        self,
        user_message: str,
        history: list[dict[str, Any]],
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """
        Run one conversation turn (may invoke multiple tool calls internally).

        Args:
            user_message: The user's latest message.
            history: Previous messages in Anthropic format.
            on_text: Optional callback invoked per streaming text chunk.
                     When provided, the underlying API call uses streaming.
            on_tool: Optional callback invoked before each tool call with
                     (tool_name, args).

        Returns:
            (response_text, updated_history)
        """
        history = history + [{"role": "user", "content": user_message}]
        response_text = ""

        while True:
            if on_text is not None:
                # Streaming path
                with self._client.messages.stream(
                    model=self.model,
                    max_tokens=8096,
                    system=self._system_prompt_blocks(),
                    tools=TOOL_SCHEMAS,
                    messages=history,
                ) as stream:
                    for chunk in stream.text_stream:
                        on_text(chunk)
                        response_text += chunk
                    response = stream.get_final_message()
            else:
                # Non-streaming path (bots / single-query)
                response = self._client.messages.create(
                    model=self.model,
                    max_tokens=8096,
                    system=self._system_prompt_blocks(),
                    tools=TOOL_SCHEMAS,
                    messages=history,
                )
                response_text = "".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", "") == "text"
                )

            history = history + [{"role": "assistant", "content": response.content}]

            if response.stop_reason != "tool_use":
                break

            # Execute tool calls and feed results back
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                if on_tool is not None:
                    on_tool(block.name, dict(block.input))
                try:
                    result = self._execute_tool(block.name, dict(block.input))
                except PermissionError as exc:
                    result = f"[Permission denied] {exc}"
                except Exception as exc:
                    result = f"[Unexpected error] {exc}"
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

            history = history + [{"role": "user", "content": tool_results}]
            response_text = ""  # reset — we'll get new text in the next iteration

        return response_text, history

    def run_query(self, query: str) -> str:
        """Single-shot query with no conversation history."""
        text, _ = self.run_turn(query, [])
        return text


# ---------------------------------------------------------------------------
# Interactive CLI chat session
# ---------------------------------------------------------------------------


def run_chat_loop(agent: HpcAgent) -> int:
    """Run an interactive readline-based chat loop in the terminal."""
    try:
        import readline  # noqa: F401  — enables Ctrl-A/E, history on supported platforms
    except ImportError:
        pass

    history: list[dict[str, Any]] = []
    print(
        f"HPC Pilot AI  [model: {agent.model} | role: {agent.role.value}]"
        "\nType 'exit' or press Ctrl-D to quit.\n"
    )

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if user_input.lower() in ("exit", "quit", "q"):
            break
        if not user_input:
            continue

        print("Agent: ", end="", flush=True)
        try:
            _, history = agent.run_turn(
                user_input,
                history,
                on_text=lambda chunk: print(chunk, end="", flush=True),
                on_tool=lambda name, args: print(
                    f"\n  [→ {name}]", end=" ", flush=True
                ),
            )
        except KeyboardInterrupt:
            print("\n(interrupted)")
        except Exception as exc:
            print(f"\nError: {exc}")
        print()

    return 0
