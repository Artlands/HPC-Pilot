"""
HPC Pilot Gateway — Telegram and Discord bot server.

Reads platform tokens from ~/.hpc-pilot/.env:
    TELEGRAM_BOT_TOKEN=...
    DISCORD_BOT_TOKEN=...

Start with:
    hpc-pilot gateway --start
    hpc-pilot-gateway --start        (script alias)

Each user/chat gets an isolated conversation session.  The same RBAC and
audit rules that apply to the CLI apply to every bot-originated tool call.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any, Callable, Optional

# Local-name re-exports so existing tests can patch
# `hpc_pilot.gateway.init_home` and `hpc_pilot.gateway.init_config`.
from hpc_pilot.paths import ensure_layout as init_home  # noqa: F401
from hpc_pilot.config import init_config  # noqa: F401
from hpc_pilot.paths import get_home


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk_message(text: str, limit: int) -> list[str]:
    """Split a message into chunks no longer than *limit* characters."""
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:limit])
        text = text[limit:]
    return chunks


def _load_env() -> None:
    from hpc_pilot.agent import _load_env as _agent_load_env
    _agent_load_env()


def _make_agent() -> Any:
    from hpc_pilot.agent import HpcAgent
    model = os.environ.get("HPC_PILOT_MODEL", "claude-opus-4-7")
    return HpcAgent(model=model)


# ---------------------------------------------------------------------------
# Telegram gateway
# ---------------------------------------------------------------------------


class TelegramGateway:
    """Wraps python-telegram-bot to route messages to HpcAgent."""

    def __init__(self, token: str, agent_factory: Callable[[], Any]) -> None:
        self.token = token
        self.agent_factory = agent_factory
        self.sessions: dict[int, tuple[Any, list[dict[str, Any]]]] = {}

    async def _start(self, update: Any, context: Any) -> None:
        chat_id = update.effective_chat.id
        self.sessions[chat_id] = (self.agent_factory(), [])
        await update.message.reply_text(
            "🖥️ *HPC Pilot connected.*\n"
            "Ask me anything about your cluster: nodes, jobs, health, Spack, Ansible…\n"
            "Commands: /reset — clear conversation",
            parse_mode="Markdown",
        )

    async def _reset(self, update: Any, context: Any) -> None:
        chat_id = update.effective_chat.id
        self.sessions[chat_id] = (self.agent_factory(), [])
        await update.message.reply_text("Conversation reset.")

    async def _handle_message(self, update: Any, context: Any) -> None:
        chat_id = update.effective_chat.id
        if chat_id not in self.sessions:
            self.sessions[chat_id] = (self.agent_factory(), [])
        agent, history = self.sessions[chat_id]

        await update.message.chat.send_action("typing")
        try:
            text, history = await asyncio.to_thread(
                agent.run_turn, update.message.text, history
            )
            self.sessions[chat_id] = (agent, history)
        except Exception as exc:
            text = f"⚠️ Error: {exc}"

        for chunk in _chunk_message(text or "(no response)", 4096):
            await update.message.reply_text(chunk)

    async def run_async(self) -> None:
        from telegram.ext import (  # type: ignore[import]
            Application,
            CommandHandler,
            MessageHandler,
            filters,
        )

        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self._start))
        app.add_handler(CommandHandler("reset", self._reset))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        print("Telegram: polling started", flush=True)
        async with app:
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            try:
                await asyncio.Event().wait()  # block until cancelled
            finally:
                await app.updater.stop()
                await app.stop()


# ---------------------------------------------------------------------------
# Discord gateway
# ---------------------------------------------------------------------------


class DiscordGateway:
    """Wraps discord.py to route DMs and @mentions to HpcAgent."""

    def __init__(self, token: str, agent_factory: Callable[[], Any]) -> None:
        self.token = token
        self.agent_factory = agent_factory
        self.sessions: dict[int, tuple[Any, list[dict[str, Any]]]] = {}

    async def run_async(self) -> None:
        import discord  # type: ignore[import]

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        sessions = self.sessions
        agent_factory = self.agent_factory

        @client.event
        async def on_ready() -> None:
            print(f"Discord: logged in as {client.user}", flush=True)

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user:
                return
            # Respond to DMs or explicit @mentions only
            if message.guild is not None and client.user not in message.mentions:
                return

            user_id = message.author.id
            if user_id not in sessions:
                sessions[user_id] = (agent_factory(), [])
            agent, history = sessions[user_id]

            async with message.channel.typing():
                try:
                    text, history = await asyncio.to_thread(
                        agent.run_turn, message.clean_content, history
                    )
                    sessions[user_id] = (agent, history)
                except Exception as exc:
                    text = f"⚠️ Error: {exc}"

            for chunk in _chunk_message(text or "(no response)", 2000):
                await message.channel.send(chunk)

        print("Discord: connecting…", flush=True)
        async with client:
            await client.start(self.token)


# ---------------------------------------------------------------------------
# Gateway orchestrator
# ---------------------------------------------------------------------------


async def _run_gateway_async() -> int:
    _load_env()

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    dc_token = os.environ.get("DISCORD_BOT_TOKEN")

    if not tg_token and not dc_token:
        print(
            "No platform tokens found. Set at least one of:\n"
            "  TELEGRAM_BOT_TOKEN or DISCORD_BOT_TOKEN\n"
            "in ~/.hpc-pilot/.env or your shell environment.",
            file=sys.stderr,
        )
        return 1

    tasks = []

    if tg_token:
        try:
            gw = TelegramGateway(tg_token, _make_agent)
            tasks.append(asyncio.create_task(gw.run_async(), name="telegram"))
        except ImportError:
            print(
                "Telegram: python-telegram-bot not installed. "
                "Run: pip install 'hpc-pilot[telegram]'",
                file=sys.stderr,
            )

    if dc_token:
        try:
            gw_dc = DiscordGateway(dc_token, _make_agent)
            tasks.append(asyncio.create_task(gw_dc.run_async(), name="discord"))
        except ImportError:
            print(
                "Discord: discord.py not installed. "
                "Run: pip install 'hpc-pilot[discord]'",
                file=sys.stderr,
            )

    if not tasks:
        print("No gateway tasks could be started.", file=sys.stderr)
        return 1

    print(f"Gateway running ({len(tasks)} platform(s)). Press Ctrl-C to stop.")
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    return 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the hpc-pilot-gateway script and `hpc-pilot gateway`."""
    parser = argparse.ArgumentParser(
        prog="hpc-pilot-gateway",
        description="HPC Pilot Gateway — Telegram and Discord bot server",
    )
    parser.add_argument("--start", action="store_true", help="Start gateway (default action)")
    parser.add_argument("--stop", action="store_true", help="Stop gateway")
    parser.add_argument("--status", action="store_true", help="Show gateway status")
    parser.add_argument("--setup", action="store_true", help="Print setup instructions")
    parser.add_argument("--port", type=int, default=8000, help="(reserved for future web UI)")
    parser.add_argument("--host", default="127.0.0.1", help="(reserved for future web UI)")

    args = parser.parse_args(argv)

    init_home()
    init_config()

    if args.setup:
        print("Gateway setup:\n")
        print("  1. Create ~/.hpc-pilot/.env and add your tokens:")
        print("       ANTHROPIC_API_KEY=sk-ant-...")
        print("       TELEGRAM_BOT_TOKEN=...    # from @BotFather")
        print("       DISCORD_BOT_TOKEN=...     # from Discord Developer Portal")
        print()
        print("  2. Set your role (admin recommended for the gateway):")
        print("       HPC_PILOT_ROLE=admin")
        print()
        print("  3. Start the gateway:")
        print("       hpc-pilot gateway --start")
        return 0

    if args.stop:
        print("Gateway: not running (send SIGINT to the gateway process to stop it).")
        return 0

    if args.status:
        _load_env()
        tg = "configured" if os.environ.get("TELEGRAM_BOT_TOKEN") else "not configured"
        dc = "configured" if os.environ.get("DISCORD_BOT_TOKEN") else "not configured"
        print(f"Telegram : {tg}")
        print(f"Discord  : {dc}")
        return 0

    # Default: --start (or bare invocation)
    try:
        return asyncio.run(_run_gateway_async())
    except KeyboardInterrupt:
        print("\nGateway stopped.")
        return 0
