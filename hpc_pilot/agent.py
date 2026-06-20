"""
HPC Pilot AI agent — Hermes Agent-powered tool-use loop for cluster management.

The HpcAgent delegates to the ``hermes`` CLI subprocess so that HPC-Pilot
tools (registered by the hpc-pilot Hermes plugin at
``~/.hermes/plugins/hpc-pilot/``) are available to any model provider
that Hermes supports (OpenAI, Anthropic, Gemini, DeepSeek, etc.).

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
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

# Import all tool modules so their @hpc_tool decorators fire at import
# time and populate the canonical registry.  TOOL_SCHEMAS is then rebuilt
# from the registry each time it's accessed.
# ---------------------------------------------------------------------------
import hpc_pilot.tools.ansible  # noqa: F401  register tools
import hpc_pilot.tools.evolve  # noqa: F401
import hpc_pilot.tools.health  # noqa: F401
import hpc_pilot.tools.jobs  # noqa: F401
import hpc_pilot.tools.metrics  # noqa: F401
import hpc_pilot.tools.multi  # noqa: F401
import hpc_pilot.tools.slurm  # noqa: F401
import hpc_pilot.tools.spack  # noqa: F401
import hpc_pilot.tools.system  # noqa: F401
import hpc_pilot.tools.warewulf  # noqa: F401
from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role, get_role
from hpc_pilot.tools._registry import get_tool_schemas


class _LazySchemas(list):
    """Self-populating list proxy that calls get_tool_schemas() on first access."""

    _loaded: bool = False

    def __init__(self) -> None:
        super().__init__()

    def _ensure(self) -> None:
        if not self._loaded:
            self.clear()
            self.extend(get_tool_schemas())
            type(self)._loaded = True

    def __iter__(self):
        self._ensure()
        return super().__iter__()

    def __len__(self):
        self._ensure()
        return super().__len__()

    def __getitem__(self, index):
        self._ensure()
        return super().__getitem__(index)


TOOL_SCHEMAS: list[dict[str, Any]] = _LazySchemas()


# ---------------------------------------------------------------------------


def _find_hermes() -> str:
    """Locate the ``hermes`` binary."""
    for path in os.environ.get("PATH", "").split(os.pathsep):
        full = os.path.join(path, "hermes")
        if os.path.isfile(full) and os.access(full, os.X_OK):
            return full
    # Last resort — hope it's on PATH
    return "hermes"


def _load_env() -> None:
    """Load environment from ~/.hpc-pilot/.env (silent if dotenv not installed)."""
    try:
        from dotenv import load_dotenv

        env_file = os.path.join(get_home(), ".env")
        if os.path.exists(env_file):
            load_dotenv(env_file, override=False)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# HpcAgent
# ---------------------------------------------------------------------------


class HpcAgent:
    """AI agent for HPC cluster management, powered by Hermes Agent.

    ``run_query`` uses ``hermes chat -q`` for single-shot queries.
    ``run_turn`` is available for programmatic multi-turn use but note that
    Hermes manages its own conversation state — the ``history`` parameter is
    informational and the returned history is a best-effort representation.

    For the full interactive chat experience (streaming, tool previews, session
    persistence), use the ``chat_command`` in ``cli.py`` which execs
    ``hermes chat -t hpc`` directly.
    """

    def __init__(
        self,
        model: str | None = None,
        role: Role | None = None,
        actor: str | None = None,
    ) -> None:
        _load_env()
        self.model = model or os.environ.get("HPC_PILOT_MODEL") or "claude-opus-4-7"
        self.role: Role = role if role is not None else get_role()
        self.actor: str = (
            actor or os.environ.get("HPC_PILOT_ACTOR") or os.environ.get("USER", "cli")
        )
        # Tracked across run_turn calls for multi-turn conversations.
        # When set, subsequent calls use --resume so Hermes maintains
        # session state across turns.
        self._hermes_session_id: str | None = None

    def run_turn(
        self,
        user_message: str,
        history: list[dict[str, Any]],
        on_text: Callable[[str], None] | None = None,
        on_tool: Callable[[str, dict[str, Any]], None] | None = None,
        on_result: Callable[[str, str], None] | None = None,
        max_iterations: int = 25,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run one conversation turn via ``hermes chat -q``.

        Multi-turn support: when called repeatedly on the same HpcAgent
        instance, subsequent calls pass ``--resume <session_id>`` so Hermes
        maintains actual conversation state across turns.

        Returns (response_text, updated_history).
        """
        messages = list(history) + [{"role": "user", "content": user_message}]

        hermes_bin = _find_hermes()
        cmd = [hermes_bin, "chat", "-q", user_message, "-t", "hpc", "--quiet"]
        if self._hermes_session_id:
            cmd.extend(["--resume", self._hermes_session_id])
        if self.model:
            cmd.extend(["-m", self.model])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except FileNotFoundError:
            text = "[Hermes Agent not found. Install with: pip install hermes-agent]"
            if on_text:
                on_text(text)
            return text, messages

        # Parse session_id from stderr (hermes always prints it at the end)
        self._parse_session_id(proc.stderr)

        if proc.returncode != 0:
            text = f"[Hermes Agent error: {proc.stderr.strip() or 'exit ' + str(proc.returncode)}]"
            if on_text:
                on_text(text)
            return text, messages

        text = proc.stdout.strip() or "(no response)"

        if on_text:
            on_text(text)

        assistant_msg: dict[str, Any] = {"role": "assistant", "content": text}
        messages = messages + [assistant_msg]
        return text, messages

    def _parse_session_id(self, stderr: str) -> None:
        """Extract and store session_id from hermes stderr output."""
        for line in stderr.splitlines():
            line = line.strip()
            if line.startswith("session_id:"):
                sid = line.split(":", 1)[1].strip()
                if sid:
                    self._hermes_session_id = sid
                break

    def reset_session(self) -> None:
        """Clear the tracked Hermes session ID so the next call starts fresh."""
        self._hermes_session_id = None

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        """Execute one tool call: RBAC -> audit -> dispatch."""
        from hpc_pilot.dispatch import invoke

        try:
            return invoke(
                name,
                args,
                role=self.role,
                actor=self.actor,
                dry_run=bool(args.get("dry_run", False)),
            )
        except RuntimeError as exc:
            return f"[Tool error] {exc}"
        except ValueError as exc:
            return f"[Input error] {exc}"
        except PermissionError:
            raise

    def run_query(self, query: str) -> str:
        """Single-shot query with no conversation history."""
        text, _ = self.run_turn(query, [])
        return text


# ---------------------------------------------------------------------------
# Session persistence (unchanged)
# ---------------------------------------------------------------------------


def _session_path(session_id: str) -> str:
    from hpc_pilot.paths import sessions_dir

    return os.path.join(sessions_dir(), f"{session_id}.json")


def _new_session_id() -> str:
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
    """Persist history to ~/.hpc-pilot/sessions/<id>.json."""
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
    path = _session_path(session_id)
    with open(path) as f:
        data: dict[str, Any] = json.load(f)
    messages: list[dict[str, Any]] = data.pop("messages", [])
    return messages, data


def list_sessions() -> list[dict[str, Any]]:
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
            summaries.append(
                {
                    "id": data.get("id", fname[:-5]),
                    "ts": float(data.get("ts", 0)),
                    "model": str(data.get("model", "")),
                    "role": str(data.get("role", "")),
                    "actor": str(data.get("actor", "")),
                    "turn_count": sum(
                        1 for m in data.get("messages", []) if m.get("role") == "user"
                    ),
                }
            )
        except Exception:
            continue
    summaries.sort(key=lambda s: s["ts"], reverse=True)
    return summaries


# ---------------------------------------------------------------------------
# HPC Pilot skin — custom Hermes skin with HPC-themed branding
# ---------------------------------------------------------------------------

_HPC_SKIN_NAME = "hpc-pilot"


def _skin_resource_path() -> pathlib.Path:
    """Return the path to the skin YAML shipped with the HPC Pilot package."""
    return pathlib.Path(__file__).parent / "hermes_plugin" / "skin.yaml"


def _ensure_hpc_pilot_skin() -> None:
    """Install and activate the HPC Pilot custom Hermes skin.

    Copies the skin YAML from the package to ``~/.hermes/skins/hpc-pilot.yaml``
    if it doesn't already exist, then sets ``display.skin: hpc-pilot`` in the
    Hermes config via ``hermes config set``.
    """
    hermes_home = pathlib.Path.home() / ".hermes"
    skins_dir = hermes_home / "skins"
    skin_file = skins_dir / f"{_HPC_SKIN_NAME}.yaml"
    skins_dir.mkdir(parents=True, exist_ok=True)

    # Copy skin YAML from the package if not already installed
    if not skin_file.exists():
        src = _skin_resource_path()
        if src.exists():
            skin_file.write_bytes(src.read_bytes())

    # Use hermes config set to activate the skin — this preserves the
    # rest of the user's config (comments, ordering, etc.) unlike a
    # yaml.safe_load + yaml.dump round-trip.
    _activate_skin_via_hermes()


def _activate_skin_via_hermes() -> None:
    """Set display.skin in Hermes config using hermes config set."""
    try:
        hermes_bin = _find_hermes()
        subprocess.run(
            [hermes_bin, "config", "set", "display.skin", _HPC_SKIN_NAME],
            capture_output=True,
            timeout=15,
        )
    except Exception:
        pass  # non-fatal — skin file is on disk, user can /skin hpc-pilot manually


# ---------------------------------------------------------------------------
# Interactive CLI chat loop  (unchanged from original)
# ---------------------------------------------------------------------------


def run_chat_loop(agent: HpcAgent, initial_history: list[dict[str, Any]] | None = None) -> int:
    """Start an interactive Hermes chat session with HPC tools loaded.

    Delegates to ``hermes chat -t hpc`` for the full streaming experience
    with tool previews, session persistence, and model/provider switching.
    """
    _ensure_hpc_pilot_skin()

    hermes_bin = _find_hermes()
    cmd = [hermes_bin, "chat", "-t", "hpc"]
    if agent.model:
        cmd.extend(["-m", agent.model])

    os.environ.setdefault("HPC_PILOT_ACTOR", agent.actor)
    os.environ.setdefault("HPC_PILOT_ROLE", agent.role.value)

    print(f"HPC Pilot -> Hermes Agent  [model: {agent.model} | role: {agent.role.value}]")
    print()

    try:
        os.execvp(hermes_bin, cmd)
    except FileNotFoundError:
        print(
            "Hermes Agent not found. Install with: pip install hermes-agent",
            file=sys.stderr,
        )
        return 1
    except OSError as exc:
        print(f"Error starting Hermes Agent: {exc}", file=sys.stderr)
        return 1
    return 0  # unreachable — exec replaces the process
