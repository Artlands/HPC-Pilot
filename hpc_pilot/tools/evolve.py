"""
Self-evolve — generate new HPC-Pilot tools, register them, and open a PR.

When the AI agent encounters a task no existing tool can handle, it calls
``hpc_self_evolve`` to generate new tool code, tests, schemas, register
everything, run tests, commit, push, and open a GitHub pull request.

Usage (from the AI agent):
    hpc_self_evolve(
        tool_name="hpc_network_ib_list_partitions",
        description="List InfiniBand partition keys on a node",
        code='''
def hpc_network_ib_list_partitions(node: str = "", *, cluster: str = "default") -> str:
    \"\"\"List InfiniBand partition keys on *node*.\"\"\"
    _validate(node, "node name")
    cl = _resolve_cluster(cluster)
    return _run(["ibstat", node] if node else ["ibstat"], cluster=cl)
''',
        test_code='''
@patch("hpc_pilot.tools.evolved.hpc_network_ib_list_partitions._run")
@patch("hpc_pilot.tools.evolved.hpc_network_ib_list_partitions._resolve_cluster")
def test_happy_path(mock_cl, mock_run):
    from hpc_pilot.tools.evolved.hpc_network_ib_list_partitions import hpc_network_ib_list_partitions
    mock_cl.return_value = MagicMock()
    mock_cl.return_value.ssh = None
    mock_run.return_value = "CA: 1 ports: 2"
    result = hpc_network_ib_list_partitions("node01")
    assert "CA:" in result
''',
        schema={
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "Node name"},
                "cluster": {"type": "string"},
            },
        },
        required_role="VIEWER",
        dry_run=True,
    )
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from typing import Any

from hpc_pilot.paths import get_home
from hpc_pilot.tools._run import _resolve_cluster, _run  # noqa: F401 — reused by generated code
from hpc_pilot.tools._validation import _NAME_RE, _USER_RE, _validate  # noqa: F401

# ---------------------------------------------------------------------------
# Valid tool name pattern
# ---------------------------------------------------------------------------

_TOOL_NAME_RE = re.compile(r"^hpc_[a-z][a-z0-9_]*[a-z0-9]$")


def _validate_tool_name(name: str) -> None:
    if not _TOOL_NAME_RE.match(name):
        raise ValueError(
            f"Invalid tool name: {name!r}. Must match 'hpc_<domain>_<action>' "
            r"(e.g. hpc_network_ib_list_partitions)"
        )


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_EVOLVED_DIR = os.path.join(os.path.dirname(__file__), "evolved")
_TESTS_EVOLVED_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "tests", "tools", "evolved"
)
_TOOLS_INIT = os.path.join(os.path.dirname(__file__), "__init__.py")
_AGENT_PATH = os.path.join(os.path.dirname(__file__), "..", "agent.py")
_DISPATCH_PATH = os.path.join(os.path.dirname(__file__), "..", "dispatch.py")
_RBAC_PATH = os.path.join(os.path.dirname(__file__), "..", "rbac.py")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Code generation helpers
# ---------------------------------------------------------------------------


def _tool_file_path(tool_name: str) -> str:
    return os.path.join(_EVOLVED_DIR, f"{tool_name}.py")


def _test_file_path(tool_name: str) -> str:
    return os.path.join(_TESTS_EVOLVED_DIR, f"test_{tool_name}.py")


def _build_tool_py(tool_name: str, code: str) -> str:
    """Wrap the user-provided *code* with the standard imports."""
    # Strip leading/trailing whitespace, dedent if needed
    lines = code.strip().splitlines()
    while lines and (not lines[0] or lines[0].strip() == ""):
        lines.pop(0)
    body = "\n".join(lines)
    if body.startswith("def "):
        body = "\n\n" + body
    return (
        '"""Auto-generated tool: {}."""\n'
        "from __future__ import annotations\n\n"
        "from hpc_pilot.tools._run import _resolve_cluster, _run\n"
        "from hpc_pilot.tools._validation import _NAME_RE, _USER_RE, _validate\n\n"
        "{}"
    ).format(tool_name, body)


def _build_test_py(tool_name: str, domain: str, test_code: str) -> str:
    """Wrap the test code with standard imports."""
    lines = test_code.strip().splitlines()
    while lines and (not lines[0] or lines[0].strip() == ""):
        lines.pop(0)
    body = "\n".join(lines)
    return (
        '"""Tests for auto-generated tool {}."""\n'
        "from __future__ import annotations\n\n"
        "from unittest.mock import MagicMock, patch\n\n"
        "import pytest\n\n"
        "{}"
    ).format(tool_name, body)


def _build_schema_entry(tool_name: str, description: str, schema: dict) -> str:
    """Build a Python dict literal for the TOOL_SCHEMAS entry."""
    props_json = json.dumps(schema.get("properties", {}), indent=12)
    required = schema.get("required", [])
    required_str = json.dumps(required) if required else ""
    return (
        '    {\n'
        f'        "name": "{tool_name}",\n'
        f'        "description": "{description}",\n'
        '        "input_schema": {{\n'
        '            "type": "object",\n'
        f'            "properties": {props_json},\n'
        f'            "required": {required_str},\n'
        '        }},\n'
        '    },'
    )


def _build_dispatch_entry(tool_name: str) -> str:
    """Build a dispatch lambda for a simple evolved tool."""
    return (
        f'    "{tool_name}": lambda args, t: t.{tool_name}(\n'
        f'        cluster=_cl(args),\n'
        f'        dry_run=_dr(args),\n'
        f'    ),'
    )


# ---------------------------------------------------------------------------
# File patching helpers
# ---------------------------------------------------------------------------


def _append_to_file(path: str, text: str) -> None:
    """Append *text* before the last line of the file (usually before ``]`` or ``}``)."""
    with open(path) as f:
        content = f.read()

    # Try to find the last non-blank, non-comment line
    lines = content.splitlines()
    # Walk backwards to find the last meaningful line
    insert_at = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped and not stripped.startswith("#"):
            # This is a closing brace/bracket — insert before it
            insert_at = i
            break
        elif stripped:
            insert_at = i

    # Insert the new text before the last line
    content_lines = content.split("\n")
    before = "\n".join(content_lines[:insert_at])
    after = "\n".join(content_lines[insert_at:])
    new_content = before + "\n" + text + "\n" + after

    with open(path, "w") as f:
        f.write(new_content)


def _add_to_rbac(tool_name: str, role: str) -> str:
    """Add an RBAC entry for the new tool."""
    line = f'    "{tool_name}": Role.{role},'
    # Insert before the closing }
    content = _read_file(_RBAC_PATH)
    insert_at = content.rfind("}")
    if insert_at == -1:
        raise RuntimeError("Could not find closing '}' in rbac.py")
    new_content = content[:insert_at] + line + "\n" + content[insert_at:]
    _write_file(_RBAC_PATH, new_content)
    return f"Added {tool_name} -> Role.{role} to rbac.py"


def _read_file(path: str) -> str:
    with open(path) as f:
        return f.read()


def _write_file(path: str, content: str) -> None:
    with open(path, "w") as f:
        f.write(content)


def _add_to_init(tool_name: str) -> str:
    """Add the tool to tools/__init__.py imports."""
    import_line = f"from hpc_pilot.tools.evolved.{tool_name} import {tool_name}  # noqa: F401"
    content = _read_file(_TOOLS_INIT)
    # Find the last import block and append
    lines = content.splitlines()
    insert_at = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        stripped = lines[i].strip()
        if stripped.startswith("from hpc_pilot.tools."):
            insert_at = i + 1
            break
    lines.insert(insert_at, import_line)
    _write_file(_TOOLS_INIT, "\n".join(lines) + "\n")
    return f"Added {import_line} to tools/__init__.py"


def _add_to_agent_schemas(tool_name: str, description: str, schema: dict) -> str:
    """Add a schema entry to TOOL_SCHEMAS in agent.py."""
    entry = _build_schema_entry(tool_name, description, schema)
    content = _read_file(_AGENT_PATH)
    # Insert before the closing `]`
    insert_at = content.rfind("\n]")
    if insert_at == -1:
        raise RuntimeError("Could not find closing ']' in agent.py")
    new_content = content[:insert_at] + "\n" + entry + "\n" + content[insert_at + 1:]
    _write_file(_AGENT_PATH, new_content)
    return f"Added schema for {tool_name} to agent.py"


def _add_to_dispatch(tool_name: str) -> str:
    """Add a dispatch entry for the tool."""
    entry = _build_dispatch_entry(tool_name)
    content = _read_file(_DISPATCH_PATH)
    # Insert before the closing `}`
    insert_at = content.rfind("\n}")
    if insert_at == -1:
        raise RuntimeError("Could not find closing '}}' in dispatch.py")
    new_content = content[:insert_at] + "\n" + entry + "\n" + content[insert_at + 1:]
    _write_file(_DISPATCH_PATH, new_content)
    return f"Added dispatch entry for {tool_name} to dispatch.py"


# ---------------------------------------------------------------------------
# Git and GitHub helpers
# ---------------------------------------------------------------------------


def _get_remote_url() -> str:
    """Read the origin remote URL from .git/config."""
    git_config = os.path.join(_PROJECT_ROOT, ".git", "config")
    with open(git_config) as f:
        for line in f:
            m = re.match(r'\s*url\s*=\s*(.+)', line.strip())
            if m:
                return m.group(1).strip()
    return ""


def _get_github_repo() -> str:
    """Extract owner/repo from the git remote URL."""
    url = _get_remote_url()
    # Handle https://github.com/owner/repo.git
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    # Handle git@github.com:owner/repo.git
    return ""


def _git_commit_and_push(tool_name: str, branch: str) -> tuple[str, str]:
    """Commit generated files and push to a new branch. Returns (sha, branch)."""
    # Create and switch to new branch
    subprocess.run(
        ["git", "checkout", "-b", branch],
        cwd=_PROJECT_ROOT, capture_output=True, text=True, check=True,
    )

    # Stage all generated files
    subprocess.run(
        ["git", "add", _EVOLVED_DIR, _TESTS_EVOLVED_DIR,
         _TOOLS_INIT, _AGENT_PATH, _DISPATCH_PATH, _RBAC_PATH],
        cwd=_PROJECT_ROOT, capture_output=True, text=True, check=True,
    )

    # Commit
    result = subprocess.run(
        ["git", "commit",
         "-m", f"Auto-generate {tool_name} via self-evolve",
         "-m", f"Generated by hpc_self_evolve to address an uncovered operational scenario."],
        cwd=_PROJECT_ROOT, capture_output=True, text=True, check=True,
    )
    sha = ""
    m = re.search(r"\[[^\]]+\] ([a-f0-9]+)", result.stdout)
    if m:
        sha = m.group(1)

    # Push
    subprocess.run(
        ["git", "push", "origin", branch],
        cwd=_PROJECT_ROOT, capture_output=True, text=True,
    )

    return sha, branch


def _create_github_pr(tool_name: str, description: str, branch: str) -> str:
    """Open a pull request via the GitHub API."""
    repo = _get_github_repo()
    if not repo:
        return "Cannot determine GitHub repository from git remote"

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        # Try reading from .env
        env_path = os.path.join(get_home(), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    m = re.match(r'GITHUB_TOKEN=(.+)', line.strip())
                    if m:
                        token = m.group(1)
                        break

    if not token:
        return (
            "GITHUB_TOKEN not configured. Set it in your environment or "
            f"in {get_home()}/.env to enable automatic PR creation.\n"
            f"Branch '{branch}' has been pushed. Create the PR manually at:\n"
            f"  https://github.com/{repo}/pull/new/{branch}"
        )

    pr_title = f"Auto-generate {tool_name} via self-evolve"
    pr_body = (
        f"## Summary\n"
        f"Automatically generated tool `{tool_name}` to address the following "
        f"gap:\n\n{description}\n\n"
        f"## What was generated\n"
        f"- `hpc_pilot/tools/evolved/{tool_name}.py` — tool implementation\n"
        f"- `tests/tools/evolved/test_{tool_name}.py` — tests\n"
        f"- Registration in `__init__.py`, `agent.py`, `dispatch.py`, `rbac.py`\n\n"
        f"## Verification\n"
        f"- [ ] `python3 -m pytest tests/ -q` passes\n"
        f"- [ ] Schema validation tests pass\n"
        f"- [ ] Tool is callable via Hermes Agent\n\n"
        f"---\n"
        f"_Generated by `hpc_self_evolve`_"
    )

    payload = json.dumps({
        "title": pr_title,
        "body": pr_body,
        "head": branch,
        "base": "main",
    }).encode()

    result = subprocess.run(
        [
            "curl", "-s", "-X", "POST",
            f"https://api.github.com/repos/{repo}/pulls",
            "-H", f"Authorization: Bearer {token}",
            "-H", "Content-Type: application/json",
            "-d", payload,
        ],
        capture_output=True, text=True, timeout=30,
    )

    if result.returncode != 0:
        return f"Failed to call GitHub API: {result.stderr}"

    resp = json.loads(result.stdout)
    if "html_url" in resp:
        return f"PR created: {resp['html_url']}"
    elif "message" in resp:
        return f"GitHub API error: {resp['message']}"
    return f"Unknown GitHub API response: {result.stdout[:500]}"


# ---------------------------------------------------------------------------
# Main self-evolve function
# ---------------------------------------------------------------------------


def hpc_self_evolve(
    tool_name: str,
    description: str,
    code: str,
    test_code: str,
    schema: dict[str, Any],
    required_role: str = "VIEWER",
    *,
    dry_run: bool = True,
    cluster: str = "default",
) -> str:
    """Generate a new HPC-Pilot tool, register it, and run tests.

    After generation, call ``hpc_self_evolve_create_pr`` separately to
    commit, push, and open a GitHub pull request.

    Args:
        tool_name: Name of the new tool (e.g. ``hpc_network_ib_list_partitions``).
        description: Human-readable description of what the tool does.
        code: Python function body (the ``def hpc_...`` implementation).
        test_code: Pytest test code for the new tool.
        schema: The ``input_schema`` dict for the tool (``type``, ``properties``, ``required``).
        required_role: RBAC role — ``VIEWER``, ``OPERATOR``, ``ADMIN``, or ``SUPERADMIN``.
        dry_run: When True, show what would be generated without writing files.
        cluster: Cluster name (for the tool's cluster parameter).
    """
    _validate_tool_name(tool_name)

    allowed_roles = {"VIEWER", "OPERATOR", "ADMIN", "SUPERADMIN"}
    if required_role.upper() not in allowed_roles:
        raise ValueError(
            f"Invalid required_role: {required_role!r}. "
            f"Must be one of {sorted(allowed_roles)}"
        )

    if not description.strip():
        raise ValueError("description is required")
    if not code.strip():
        raise ValueError("code is required")
    if not test_code.strip():
        raise ValueError("test_code is required")

    role_up = required_role.upper()

    # Build generated content
    tool_py = _build_tool_py(tool_name, code)
    test_py = _build_test_py(tool_name, tool_name, test_code)

    lines: list[str] = [
        f"=== Self-Evolve Plan for '{tool_name}' ===",
        f"  Role: {role_up}",
        f"  Description: {description}",
        "",
        f"  1. Create {_tool_file_path(tool_name)}",
        f"  2. Create {_test_file_path(tool_name)}",
        f"  3. Update {_TOOLS_INIT} (add import)",
        f"  4. Update {_AGENT_PATH} (add schema)",
        f"  5. Update {_DISPATCH_PATH} (add dispatch entry)",
        f"  6. Update {_RBAC_PATH} (add {tool_name} -> {role_up})",
        "",
    ]

    if dry_run:
        lines.append("  --- Generated hpc_pilot/tools/evolved/<tool_name>.py ---")
        for line in tool_py.splitlines():
            lines.append(f"  | {line}")
        lines.append("")
        lines.append("  --- Generated tests/tools/evolved/test_<tool_name>.py ---")
        for line in test_py.splitlines():
            lines.append(f"  | {line}")
        lines.append("")
        lines.append("  DRY-RUN: No files were written.")
        lines.append("  Re-run with dry_run=False to execute.")
        return "\n".join(lines)

    # ---- Execute ----
    # 1. Create tool file
    os.makedirs(_EVOLVED_DIR, exist_ok=True)
    with open(_tool_file_path(tool_name), "w") as f:
        f.write(tool_py)
    lines.append(f"  ✓ Created {tool_name}.py")

    # 2. Create test file
    os.makedirs(_TESTS_EVOLVED_DIR, exist_ok=True)
    with open(_test_file_path(tool_name), "w") as f:
        f.write(test_py)
    lines.append(f"  ✓ Created test_{tool_name}.py")

    # 3. Update __init__.py
    result = _add_to_init(tool_name)
    lines.append(f"  ✓ {result}")

    # 4. Update agent.py schemas
    result = _add_to_agent_schemas(tool_name, description, schema)
    lines.append(f"  ✓ {result}")

    # 5. Update dispatch.py
    result = _add_to_dispatch(tool_name)
    lines.append(f"  ✓ {result}")

    # 6. Update rbac.py
    result = _add_to_rbac(tool_name, role_up)
    lines.append(f"  ✓ {result}")

    # 7. Run tests
    lines.append("")
    lines.append("  Running pytest...")
    test_result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=short"],
        cwd=_PROJECT_ROOT, capture_output=True, text=True, timeout=300,
    )

    if test_result.returncode != 0:
        lines.append(f"  ❌ Tests failed ({test_result.returncode}):")
        for line in test_result.stderr.splitlines()[-10:]:
            lines.append(f"     {line}")
        for line in test_result.stdout.splitlines()[-10:]:
            lines.append(f"     {line}")
        lines.append("")
        lines.append("  Files are on disk but tests did not pass.")
        lines.append("  Fix the errors above and run pytest manually:")
        lines.append(f"    python3 -m pytest tests/tools/evolved/test_{tool_name}.py -x --tb=long")
        return "\n".join(lines)

    lines.append("  ✓ All tests passed.")

    # 8. Git commit, push, and PR — done separately via hpc_self_evolve_create_pr
    lines.append("")
    lines.append("  ✓ Files generated and registered. All tests pass.")
    lines.append("")
    lines.append("  To commit, push, and open a PR, call:")
    lines.append(f'    hpc_self_evolve_create_pr(tool_name="{tool_name}", description="{description}")')
    lines.append("")
    lines.append("  Or to skip git/PR, the files are ready on disk:")
    lines.append(f"    {_tool_file_path(tool_name)}")
    lines.append(f"    {_test_file_path(tool_name)}")

    return "\n".join(lines)


def hpc_self_evolve_create_pr(
    tool_name: str,
    description: str = "",
    *,
    dry_run: bool = False,
    cluster: str = "default",
) -> str:
    """Commit generated tool files, push to a new branch, and open a GitHub PR.

    Call this after ``hpc_self_evolve`` has generated the files and tests
    passed. Requires ``GITHUB_TOKEN`` in the environment or ``~/.hpc-pilot/.env``.

    Args:
        tool_name: The name of the evolved tool (e.g. ``hpc_network_ib_list_partitions``).
            Must match a tool that was previously generated by ``hpc_self_evolve``.
        description: Human-readable description for the PR body.
        dry_run: Show what would be done without actually pushing.
        cluster: Cluster name (unused, for interface consistency).
    """
    _validate_tool_name(tool_name)

    # Verify the generated files exist
    tool_path = _tool_file_path(tool_name)
    test_path = _test_file_path(tool_name)
    missing = []
    if not os.path.exists(tool_path):
        missing.append(tool_path)
    if not os.path.exists(test_path):
        missing.append(test_path)
    if missing:
        return (
            "Cannot create PR — files not found:\n"
            + "\n".join(f"  ❌ {p}" for p in missing)
            + "\n\nRun hpc_self_evolve(... dry_run=False) first to generate the files."
        )

    lines: list[str] = [
        f"=== Creating PR for '{tool_name}' ===",
    ]

    branch = f"self-evolve/{tool_name}-{int(time.time())}"

    if dry_run:
        lines.append(f"  Would create branch '{branch}'")
        lines.append(f"  Would commit: {_TOOLS_INIT}, {_AGENT_PATH}, {_DISPATCH_PATH}, {_RBAC_PATH}")
        lines.append(f"  Would commit: {tool_path}, {test_path}")
        lines.append(f"  Would push to origin/{branch}")
        lines.append(f"  Would open PR via GitHub API")
        lines.append("")
        lines.append("  DRY-RUN: no files were pushed.")
        return "\n".join(lines)

    # Commit and push
    try:
        sha, branch_name = _git_commit_and_push(tool_name, branch)
        lines.append(f"  ✓ Committed {sha} to branch '{branch_name}'")
        lines.append(f"  ✓ Pushed to origin/{branch_name}")
    except subprocess.CalledProcessError as exc:
        lines.append(f"  ⚠️ Git/Push error: {exc}")
        lines.append("  Files are on disk. Push manually:")
        lines.append("    git push origin HEAD")
        return "\n".join(lines)

    # Create PR
    pr_result = _create_github_pr(tool_name, description, branch_name)
    lines.append(f"  {pr_result}")

    return "\n".join(lines)
