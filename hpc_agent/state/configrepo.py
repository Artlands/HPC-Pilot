"""Config repository wrapper with git operations.

See spec 00 §2 and spec 01 §5.

This module wraps git operations for config-as-code workflows. All mutating
tools stage + commit config changes; the commit SHA is recorded in the audit
log for revert capability.
"""

from __future__ import annotations

import os
from pathlib import Path

from hpc_agent.exec import audit
from hpc_agent.exec.runner import CommandResult, CommandSpec, run_command


class ConfigRepo:
    """Git-backed config repository wrapper.

    All config files managed by the agent live in this repo:
    - slurm/slurm.conf
    - warewulf/overlays/**
    - spack/envs/**/spack.yaml
    - ansible/playbooks/**
    """

    def __init__(self, repo_path: str | None = None):
        """Initialize config repo wrapper."""
        self.path = Path(repo_path or os.environ.get("HPC_CONFIG_REPO", "/etc/hpc-agent/config"))
        self._ensure_repo()

    def _ensure_repo(self) -> None:
        """Ensure the config repo exists and is initialized."""
        if not self.path.exists():
            self.path.mkdir(parents=True, exist_ok=True)
        if not (self.path / ".git").exists():
            self._run(["init"])

    def _run(self, args: list[str]) -> CommandResult:
        """Run a git command in the repo directory."""
        event = audit.new_event(
            actor="hpc-agent",
            tool="configrepo.git",
            risk="low",
            input={"args": args},
        )
        result = run_command(
            CommandSpec(argv=["git", *args], cwd=str(self.path), timeout_s=120),
            actor="hpc-agent",
            audit_id=event.id,
        )
        event.decision = "auto"
        event.result_status = "ok" if result.rc == 0 else "error"
        audit.commit_event(event)
        return result

    def read(self, relpath: str) -> str:
        """Read a file from the repo."""
        filepath = self.path / relpath
        if not filepath.exists():
            raise FileNotFoundError(f"File not found: {relpath}")
        return filepath.read_text()

    def write(self, relpath: str, content: str) -> None:
        """Write content to a file in the repo."""
        filepath = self.path / relpath
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)

    def stage(self, relpath: str, content: str) -> None:
        """Stage a file for commit."""
        self.write(relpath, content)
        self._run(["add", relpath])

    def diff(self) -> str:
        """Get unified diff of staged changes."""
        result = self._run(["diff", "--cached"])
        return result.stdout

    def commit(self, message: str, author: str = "hpc-agent") -> str:
        """Commit staged changes and return the commit SHA."""
        git_author = author if "<" in author and ">" in author else f"{author} <{author}@localhost>"
        result = self._run(
            [
                "-c",
                "user.name=hpc-agent",
                "-c",
                "user.email=hpc-agent@localhost",
                "commit",
                "-m",
                message,
                f"--author={git_author}",
            ]
        )
        if result.rc != 0:
            raise RuntimeError(f"Git commit failed: {result.stderr}")
        # Get the commit SHA
        result = self._run(["rev-parse", "HEAD"])
        return result.stdout.strip()

    def snapshot(self) -> str:
        """Create a tag for current HEAD and return the ref."""
        # Generate a unique tag name
        result = self._run(["rev-parse", "HEAD"])
        sha = result.stdout.strip()
        tag = f"snapshot-{sha[:12]}"
        self._run(["tag", tag])
        return f"refs/tags/{tag}"

    def rollback(self, ref: str) -> None:
        """Reset the working tree to a previous commit/tag."""
        self._run(["reset", "--hard", ref])
        self._run(["clean", "-fd"])

    def status(self) -> dict[str, list[str]]:
        """Get git status as a structured dict."""
        result = self._run(["status", "--porcelain"])
        status: dict[str, list[str]] = {
            "staged": [],
            "modified": [],
            "untracked": [],
        }
        for line in result.stdout.splitlines():
            if not line:
                continue
            code = line[:2]
            path = line[3:]
            if code == "A ":
                status["staged"].append(path)
            elif code == "M ":
                status["modified"].append(path)
            elif code == "??":
                status["untracked"].append(path)
        return status


def get_config_repo() -> ConfigRepo:
    """Get the global config repo instance."""
    return ConfigRepo()
