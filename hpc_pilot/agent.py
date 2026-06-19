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
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role, get_role

if TYPE_CHECKING:
    pass

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
        "name": "hpc_warewulf_power_reset",
        "description": (
            "Power-reset a Warewulf node so it PXE-boots from its assigned image. "
            "This is disruptive — use dry_run=true to preview first."
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
        "input_schema": {
            "type": "object",
            "properties": {
                "cluster": {
                    "type": "string",
                    "description": "Target cluster name (default: 'default')",
                },
            },
        },
    },
    {
        "name": "hpc_skill_describe",
        "description": "Return the YAML definition of a named runbook/skill.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name (e.g. 'drain-and-patch-node')",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "hpc_skill_run",
        "description": (
            "Execute a named runbook/skill with the given inputs. "
            "Returns a run record with step results and status. "
            "Use resume_run_id to continue a paused run."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill name"},
                "inputs": {
                    "type": "object",
                    "description": "Input key-value pairs required by the skill",
                },
                "cluster": {"type": "string", "description": "Target cluster (default: 'default')"},
                "resume_run_id": {
                    "type": "string",
                    "description": "Run ID of a paused skill run to resume",
                },
            },
            "required": ["name"],
        },
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
• viewer     — read-only queries (node status, queue, health, Spack, Warewulf images)
• operator   — viewer + drain/resume nodes, run skills
• admin      — operator + modify QOS, run Ansible playbooks, bootstrap Warewulf nodes
• superadmin — admin + Slurm reconfig, Warewulf bootstrap (DHCP/TFTP/NFS), accounting schema

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
        from dotenv import load_dotenv

        env_file = os.path.join(get_home(), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file, override=False)
    except ImportError:
        pass


_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    "claude-opus-4-7": 200_000,
    "claude-sonnet-4-6": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
}
_DEFAULT_CONTEXT_TOKENS = 200_000
_SUMMARIZE_THRESHOLD = 0.80  # summarize when history > 80% of model context


def _estimate_tokens(messages: list[Any]) -> int:
    """Rough token estimate: 4 chars ≈ 1 token."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    total += len(str(block.get("text", "") or block.get("content", ""))) // 4
                else:
                    total += len(str(getattr(block, "text", "") or "")) // 4
    return total


class HpcAgent:
    """Claude-powered agent that drives HPC cluster tool calls."""

    def __init__(
        self,
        model: str | None = None,
        role: Role | None = None,
        actor: str | None = None,
        summarize: bool = True,
    ) -> None:
        _load_env()
        from anthropic import Anthropic  # imported lazily so tests can stub

        from hpc_pilot.config import load_config

        cfg = load_config()
        self.model = model or os.environ.get("HPC_PILOT_MODEL") or cfg.model
        self.role: Role = role if role is not None else get_role()
        self.actor: str = (
            actor or os.environ.get("HPC_PILOT_ACTOR") or os.environ.get("USER", "cli")
        )
        self.summarize = summarize
        self._client = Anthropic()

    # ------------------------------------------------------------------
    # System prompt (with prompt-caching header)
    # ------------------------------------------------------------------

    def _system_prompt_blocks(self) -> list[dict[str, Any]]:
        text = _SYSTEM_PROMPT.format(actor=self.actor, role=self.role.value)
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    # ------------------------------------------------------------------
    # Context budget management
    # ------------------------------------------------------------------

    def _context_limit(self) -> int:
        return _MODEL_CONTEXT_TOKENS.get(self.model, _DEFAULT_CONTEXT_TOKENS)

    def _maybe_summarize(self, messages: list[Any]) -> list[Any]:
        """Summarize the oldest half of history if we're near the context limit."""
        if not self.summarize:
            return messages

        limit = self._context_limit()
        estimated = _estimate_tokens(messages)
        if estimated < int(limit * _SUMMARIZE_THRESHOLD):
            return messages

        from hpc_pilot.audit import AuditEvent, log_audit

        half = len(messages) // 2
        to_summarize = messages[:half]
        to_keep = messages[half:]

        summary_prompt = (
            "Summarize the following HPC Pilot conversation history into 1-2 paragraphs "
            "that preserve tool calls, decisions, and cluster state changes. "
            "Be concise but complete.\n\n"
            + "\n".join(
                f"{m['role']}: "
                + (m["content"] if isinstance(m["content"], str) else "[tool messages]")
                for m in to_summarize
            )
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            summary_text = "".join(
                block.text
                for block in resp.content
                if getattr(block, "type", "") == "text"
            )
            summary_msg: dict[str, Any] = {
                "role": "user",
                "content": f"[Summary of earlier conversation:] {summary_text}",
            }
            log_audit(AuditEvent(
                tool="conversation_summarize",
                actor=self.actor,
                role=self.role.value,
                args={"messages_summarized": half, "estimated_tokens_before": estimated},
                dry_run=False,
            ))
            return [summary_msg] + to_keep
        except Exception:
            return messages  # summarization failure must not break the turn

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _call_tool(self, name: str, args: dict[str, Any]) -> str:
        """Dispatch to a real tool function by name (no RBAC/audit — call _execute_tool instead)."""
        from hpc_pilot import tools
        from hpc_pilot.dispatch import _dispatch
        return _dispatch(name, args, tools)

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute one tool call: RBAC-check → audit → dispatch → return string result."""
        from hpc_pilot.dispatch import invoke

        try:
            return invoke(
                name, args, role=self.role, actor=self.actor,
                dry_run=bool(args.get("dry_run", False)),
            )
        except RuntimeError as exc:
            return f"[Tool error] {exc}"
        except ValueError as exc:
            return f"[Input error] {exc}"

    # ------------------------------------------------------------------
    # Conversation turn
    # ------------------------------------------------------------------

    def _make_api_request(
        self,
        messages: list[Any],
        on_text: Callable[[str], None] | None,
    ) -> tuple[str, Any]:
        """Make one API call with retry on transient errors.

        Returns (response_text, message_object).  Retries up to 3 times on
        RateLimitError/APIConnectionError with 1s → 2s → 4s backoff.  Streaming
        retries only if no chunks have been emitted yet (to avoid duplicate output).
        """
        import anthropic

        transient = (anthropic.RateLimitError, anthropic.APIConnectionError)
        delay = 1.0
        for attempt in range(3):
            try:
                if on_text is not None:
                    chunks: list[str] = []
                    with self._client.messages.stream(
                        model=self.model,
                        max_tokens=8096,
                        system=cast(Any, self._system_prompt_blocks()),
                        tools=cast(Any, TOOL_SCHEMAS),
                        messages=cast(Any, messages),
                    ) as stream:
                        for chunk in stream.text_stream:
                            on_text(chunk)
                            chunks.append(chunk)
                        msg: Any = stream.get_final_message()
                    return "".join(chunks), msg
                else:
                    msg = self._client.messages.create(
                        model=self.model,
                        max_tokens=8096,
                        system=cast(Any, self._system_prompt_blocks()),
                        tools=cast(Any, TOOL_SCHEMAS),
                        messages=cast(Any, messages),
                    )
                    text = "".join(
                        block.text
                        for block in msg.content
                        if getattr(block, "type", "") == "text"
                    )
                    return text, msg
            except transient:
                if attempt == 2:
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")  # mypy

    def run_turn(
        self,
        user_message: str,
        history: list[dict[str, Any]],
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any]], None] | None = None,
        on_result: Callable[[str, str], None] | None = None,
        max_iterations: int = 25,
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
            on_result: Optional callback invoked after each tool call with
                       (tool_name, result_string).
            max_iterations: Maximum number of API calls before breaking the loop.

        Returns:
            (response_text, updated_history)
        """
        from hpc_pilot.audit import log_llm_usage

        # The Anthropic SDK accepts list[dict] for messages; cast satisfies mypy.
        messages: list[Any] = list(history) + [{"role": "user", "content": user_message}]
        messages = self._maybe_summarize(messages)
        response_text = ""
        iterations = 0

        while iterations < max_iterations:
            response_text, response = self._make_api_request(messages, on_text)

            try:
                usage = getattr(response, "usage", None)
                if usage is not None:
                    log_llm_usage(
                        actor=self.actor,
                        role=self.role.value,
                        model=self.model,
                        input_tokens=int(getattr(usage, "input_tokens", 0)),
                        output_tokens=int(getattr(usage, "output_tokens", 0)),
                    )
            except Exception:
                pass  # usage logging must never block the turn

            iterations += 1
            messages = messages + [{"role": "assistant", "content": response.content}]

            if response.stop_reason != "tool_use":
                break

            # Execute tool calls and feed results back
            tool_results: list[dict[str, Any]] = []
            for block in response.content:
                if getattr(block, "type", "") != "tool_use":
                    continue
                tool_name: str = block.name
                tool_input: dict[str, Any] = dict(block.input)
                if on_tool is not None:
                    on_tool(tool_name, tool_input)
                try:
                    result = self._execute_tool(tool_name, tool_input)
                except PermissionError as exc:
                    result = f"[Permission denied] {exc}"
                except Exception as exc:
                    result = f"[Unexpected error] {exc}"
                if on_result is not None:
                    on_result(tool_name, result)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

            messages = messages + [{"role": "user", "content": tool_results}]
            response_text = ""  # reset — we'll get new text in the next iteration
        else:
            response_text = (
                f"[Stopped after {max_iterations} iterations — "
                "possible infinite tool-call loop.]"
            )

        return response_text, messages

    def run_query(self, query: str) -> str:
        """Single-shot query with no conversation history."""
        text, _ = self.run_turn(query, [])
        return text


# ---------------------------------------------------------------------------
# Session persistence
# ---------------------------------------------------------------------------


def _session_path(session_id: str) -> str:
    from hpc_pilot.paths import sessions_dir
    return os.path.join(sessions_dir(), f"{session_id}.json")


def _new_session_id() -> str:
    """Return a timestamp-based session ID that doesn't collide with existing files."""
    import datetime
    base = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    if not os.path.exists(_session_path(base)):
        return base
    for i in range(2, 100):
        candidate = f"{base}-{i}"
        if not os.path.exists(_session_path(candidate)):
            return candidate
    return base


def _serialize_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert one Anthropic history message to a plain JSON-serializable dict.

    Assistant messages carry SDK content-block objects; this converts them to
    plain dicts so they survive a round-trip through json.dump / json.load.
    """
    content = msg.get("content")
    if not isinstance(content, list):
        return dict(msg)
    blocks: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict):
            blocks.append(block)
        elif hasattr(block, "model_dump"):
            blocks.append(block.model_dump())
        elif hasattr(block, "dict"):
            blocks.append(block.dict())
        else:
            d: dict[str, Any] = {"type": getattr(block, "type", "unknown")}
            for attr in ("text", "id", "name", "input", "tool_use_id", "content"):
                val = getattr(block, attr, None)
                if val is not None:
                    d[attr] = val
            blocks.append(d)
    return {"role": msg["role"], "content": blocks}


def save_session(
    history: list[dict[str, Any]],
    agent: HpcAgent,
    session_id: str | None = None,
) -> str:
    """Persist *history* to ~/.hpc-pilot/sessions/<id>.json.

    Returns the session ID so callers can print a resume hint.
    """
    from hpc_pilot.paths import ensure_layout
    ensure_layout()
    sid = session_id or _new_session_id()
    record: dict[str, Any] = {
        "id": sid,
        "ts": time.time(),
        "model": agent.model,
        "role": agent.role.value,
        "actor": agent.actor,
        "messages": [_serialize_message(m) for m in history],
    }
    with open(_session_path(sid), "w") as f:
        json.dump(record, f, indent=2, default=str)
    return sid


def load_session(session_id: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load a saved session; return *(messages, metadata)*.

    Raises FileNotFoundError when the session does not exist.
    """
    path = _session_path(session_id)
    with open(path) as f:
        data: dict[str, Any] = json.load(f)
    messages: list[dict[str, Any]] = data.pop("messages", [])
    return messages, data


def list_sessions() -> list[dict[str, Any]]:
    """Return session summaries sorted newest-first.

    Each summary has keys: id, ts, model, role, actor, turn_count.
    """
    from hpc_pilot.paths import sessions_dir
    sdir = sessions_dir()
    if not os.path.isdir(sdir):
        return []
    summaries: list[dict[str, Any]] = []
    for fname in os.listdir(sdir):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(sdir, fname)) as f:
                data = json.load(f)
            summaries.append({
                "id": data.get("id", fname[:-5]),
                "ts": float(data.get("ts", 0)),
                "model": str(data.get("model", "")),
                "role": str(data.get("role", "")),
                "actor": str(data.get("actor", "")),
                "turn_count": sum(
                    1 for m in data.get("messages", []) if m.get("role") == "user"
                ),
            })
        except Exception:
            continue
    summaries.sort(key=lambda s: s["ts"], reverse=True)
    return summaries


# ---------------------------------------------------------------------------
# Interactive CLI chat session
# ---------------------------------------------------------------------------


def run_chat_loop(agent: HpcAgent, initial_history: list[dict[str, Any]] | None = None) -> int:
    """Run an interactive readline-based chat loop in the terminal."""
    import contextlib
    with contextlib.suppress(ImportError):
        import readline  # noqa: F401  — enables Ctrl-A/E, history on supported platforms

    history: list[dict[str, Any]] = list(initial_history) if initial_history else []
    turn_start = len(history)  # messages present before this session's turns
    print(
        f"HPC Pilot AI  [model: {agent.model} | role: {agent.role.value}]"
        "\nType 'exit' or press Ctrl-D to quit.\n"
    )

    try:
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
                def _on_tool(name: str, args: dict[str, Any]) -> None:
                    arg_str = json.dumps(args, default=str)
                    if len(arg_str) > 80:
                        arg_str = arg_str[:77] + "..."
                    print(f"\n  [→ {name}] {arg_str}", end=" ", flush=True)

                def _on_result(_name: str, result: str) -> None:
                    snippet = result[:150] + ("…" if len(result) > 150 else "")
                    print(f"\n  [← {snippet}]", end=" ", flush=True)

                _, history = agent.run_turn(
                    user_input,
                    history,
                    on_text=lambda chunk: print(chunk, end="", flush=True),
                    on_tool=_on_tool,
                    on_result=_on_result,
                )
            except KeyboardInterrupt:
                print("\n(interrupted)")
            except Exception as exc:
                print(f"\nError: {exc}")
            print()
    finally:
        if len(history) > turn_start:
            try:
                sid = save_session(history, agent)
                print(f"\nSession saved: {sid}")
                print(f"  Resume with: hpc-pilot chat --resume {sid}")
            except Exception as exc:
                print(f"\n[Warning] Could not save session: {exc}")

    return 0
