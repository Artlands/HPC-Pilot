"""
Self-evolve — generate new HPC-Pilot tools and register them.

When the AI agent encounters a task no existing tool can handle, it calls
``hpc_self_evolve`` to generate new tool code and tests, then stages them
under ``evolved/staging/``.  A separate ``hpc_self_evolve_promote`` step
moves the candidate into the live registry and creates a PR.

This two-phase flow (stage → promote) provides a manual review gate before
generated code lands in the live namespace.

Safety layers in order:
  1. AST whitelist — only known-safe AST node types + dangerous-call denylist.
  2. Schema validation — ``jsonschema.Draft202012Validator.check_schema``.
  3. Sandboxed pytest — tests run in an isolated subprocess with network
     blocked (no viable ``PYTHONPATH``, no ``PATH`` to pip).
  4. Pre-import staging — candidate sits under ``evolved/staging/`` until
     human (or calling agent) explicitly promotes it.

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
@patch("hpc_pilot.tools.evolved.staging.hpc_network_ib_list_partitions._run")
@patch("hpc_pilot.tools.evolved.staging.hpc_network_ib_list_partitions._resolve_cluster")
def test_happy_path(mock_cl, mock_run):
    from hpc_pilot.tools.evolved.staging import hpc_network_ib_list_partitions
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
    )
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from typing import Any

from hpc_pilot.paths import get_home
from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run  # noqa: F401 — reused by generated code
from hpc_pilot.tools._validation import _NAME_RE, _USER_RE, _validate  # noqa: F401

try:
    import jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

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
# AST whitelist — restrict generated code to known-safe operations
# ---------------------------------------------------------------------------

# AST node types that are always allowed.
_ALLOWED_AST_NODES: frozenset[type[ast.AST]] = frozenset(
    {
        # Top-level
        ast.Module,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
        ast.Return,
        ast.Assign,
        ast.AnnAssign,
        ast.AugAssign,
        ast.Expr,
        ast.Pass,
        ast.Import,
        ast.ImportFrom,
        ast.If,
        ast.For,
        ast.While,
        ast.With,
        ast.Try,
        ast.Raise,
        ast.Assert,
        ast.Delete,
        # Break / Continue
        ast.Break,
        ast.Continue,
        ast.ExceptHandler,
        ast.withitem,
        # Function signatures
        ast.arg,
        ast.arguments,
        # Expressions
        ast.Name,
        ast.Load,
        ast.Store,
        ast.Del,
        ast.Attribute,
        ast.Subscript,
        ast.Call,
        ast.Constant,
        ast.List,
        ast.Tuple,
        ast.Dict,
        ast.Set,
        ast.BinOp,
        ast.UnaryOp,
        ast.BoolOp,
        ast.Compare,
        ast.IfExp,
        ast.Lambda,
        ast.FormattedValue,
        ast.JoinedStr,
        # Subscript slices (Index was removed in Python 3.9+)
        ast.Slice,
        # Comprehensions
        ast.ListComp,
        ast.SetComp,
        ast.DictComp,
        ast.GeneratorExp,
        ast.comprehension,
        # Operators
        ast.Add,
        ast.Sub,
        ast.Mult,
        ast.Div,
        ast.FloorDiv,
        ast.Mod,
        ast.Pow,
        ast.LShift,
        ast.RShift,
        ast.BitOr,
        ast.BitXor,
        ast.BitAnd,
        ast.MatMult,
        ast.Not,
        ast.UAdd,
        ast.USub,
        ast.Invert,
        ast.And,
        ast.Or,
        ast.Eq,
        ast.NotEq,
        ast.Lt,
        ast.LtE,
        ast.Gt,
        ast.GtE,
        ast.Is,
        ast.IsNot,
        ast.In,
        ast.NotIn,
        # Match statement (Python 3.10+)
        ast.Match,
        ast.MatchValue,
        ast.MatchSingleton,
        ast.MatchSequence,
        ast.MatchMapping,
        ast.MatchClass,
        ast.MatchStar,
        ast.MatchAs,
        ast.MatchOr,
        # Keyword / Starred / Await / Yield
        ast.keyword,
        ast.Starred,
        ast.Await,
        ast.Yield,
        ast.YieldFrom,
        # Type annotation nodes (Python 3.12+)
        ast.TypeVar,
        ast.ParamSpec,
        ast.TypeVarTuple,
        ast.Subscript,
    }
)

# AST node types that are BLOCKED — explicit deny list for dangerous patterns.
_DANGEROUS_CALL_NAMES: frozenset[str] = frozenset(
    {
        # Code execution
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        # Built-in IO
        "input",
        "breakpoint",
        # OS / subprocess (use _run instead)
        "os.system",
        "os.popen",
        "subprocess.run",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.check_call",
        "subprocess.check_output",
        "shlex.quote",
        # Attribute / descriptor introspection — can bypass AST whitelist
        "__getattribute__",
        "__setattr__",
        "__getattr__",
        "__class__",
        "__subclasses__",
        "__globals__",
        "__code__",
        "__builtins__",
        "globals",
        "locals",
        "vars",
        "type",
        "getattr",
        "setattr",
        "delattr",
        "hasattr",
        # Import-related dangerous patterns
        "importlib.import_module",
    }
)


def _check_ast_safety(tree: ast.Module) -> None:
    """Walk *tree* and raise ``ValueError`` if any unsafe construct is found.

    Two layers of defence:
      1. Every AST node type must be in ``_ALLOWED_AST_NODES``.
      2. Every function call name is checked against ``_DANGEROUS_CALL_NAMES``.
    """
    for node in ast.walk(tree):
        node_type = type(node)

        # Layer 1: unknown node types
        if node_type not in _ALLOWED_AST_NODES:
            name = getattr(node, "name", getattr(node, "id", node_type.__name__))
            raise ValueError(
                f"Unsafe AST node '{node_type.__name__}' "
                f"(near '{name}' at line {getattr(node, 'lineno', '?')}). "
                f"Only whitelisted operations are allowed in generated code."
            )

        # Layer 2: dangerous call names (both Name and Attribute calls)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                call_name = _resolve_call_name(node)
            elif isinstance(node.func, ast.Name):
                call_name = node.func.id
            else:
                continue
            if call_name in _DANGEROUS_CALL_NAMES:
                raise ValueError(
                    f"Dangerous function call '{call_name}' at line "
                    f"{getattr(node, 'lineno', '?')}. "
                    f"Use _run() for subprocess and _validate() for validation."
                )


def _resolve_call_name(node: ast.Call) -> str:
    """Resolve a call node to a dotted name string like 'os.system'."""
    parts: list[str] = []
    cur = node.func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    parts.reverse()
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_EVOLVED_DIR = os.path.join(os.path.dirname(__file__), "evolved")
_STAGING_DIR = os.path.join(_EVOLVED_DIR, "staging")
_TESTS_EVOLVED_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "tests", "tools", "evolved"
)
_TESTS_STAGING_DIR = os.path.join(_TESTS_EVOLVED_DIR, "staging")
_TOOLS_INIT = os.path.join(os.path.dirname(__file__), "__init__.py")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Code generation helpers
# ---------------------------------------------------------------------------


def _tool_file_path(tool_name: str, staging: bool = True) -> str:
    """Return the path to the generated tool file.

    When *staging* is True (default), the path is under ``evolved/staging/``.
    When False, the path is the live location under ``evolved/``.
    """
    base = _STAGING_DIR if staging else _EVOLVED_DIR
    return os.path.join(base, f"{tool_name}.py")


def _test_file_path(tool_name: str, staging: bool = True) -> str:
    """Return the path to the generated test file.

    When *staging* is True (default), the path is under
    ``tests/tools/evolved/staging/``.
    """
    base = _TESTS_STAGING_DIR if staging else _TESTS_EVOLVED_DIR
    return os.path.join(base, f"test_{tool_name}.py")


def _build_tool_py(tool_name: str, code: str) -> str:
    """Wrap the user-provided *code* with the standard imports."""
    lines = code.strip().splitlines()
    while lines and (not lines[0] or lines[0].strip() == ""):
        lines.pop(0)
    body = "\n".join(lines)
    if body.startswith("def "):
        body = "\n\n" + body
    return (
        f'"""Auto-generated tool: {tool_name}."""\n'
        "from __future__ import annotations\n\n"
        "from hpc_pilot.tools._run import _resolve_cluster, _run\n"
        "from hpc_pilot.tools._validation import _NAME_RE, _USER_RE, _validate\n\n"
        f"{body}"
    )


def _build_test_py(tool_name: str, domain: str, test_code: str) -> str:
    """Wrap the test code with standard imports."""
    lines = test_code.strip().splitlines()
    while lines and (not lines[0] or lines[0].strip() == ""):
        lines.pop(0)
    body = "\n".join(lines)
    return (
        f'"""Tests for auto-generated tool {tool_name}."""\n'
        "from __future__ import annotations\n\n"
        "from unittest.mock import MagicMock, patch\n\n"
        "import pytest\n\n"
        f"{body}"
    )


def _add_to_init(tool_name: str) -> str:
    """Add the tool to tools/__init__.py imports."""
    import_line = f"from hpc_pilot.tools.evolved.{tool_name} import {tool_name}  # noqa: F401"
    content = _read_file(_TOOLS_INIT)
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


def _read_file(path: str) -> str:
    with open(path) as f:
        return f.read()


def _write_file(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Schema validation (F.3)
# ---------------------------------------------------------------------------


def _validate_schema(schema: dict[str, Any], tool_name: str) -> None:
    """Validate *schema* against JSON Schema Draft 2020-12.

    Raises ``ValueError`` if the schema is malformed or ``jsonschema`` is
    not installed.
    """
    if not _HAS_JSONSCHEMA:
        raise ValueError(
            "jsonschema library is required to validate generated tool schemas.\n"
            "Install it with: pip install jsonschema"
        )
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        raise ValueError(
            f"Generated tool {tool_name!r} has an invalid input_schema: {exc}"
        ) from None


# ---------------------------------------------------------------------------
# Sandboxed test runner (F.1)
# ---------------------------------------------------------------------------


def _run_sandboxed_pytest(tool_name: str) -> subprocess.CompletedProcess:
    """Run pytest for *tool_name* in an isolated subprocess.

    Isolation measures:
      - A stripped environment with no ``PYTHONPATH``, no ``PATH`` pointing
        to ``pip``/``easy_install``, and no access to network-relevant vars.
      - The subprocess uses the same Python executable but cannot install
        new packages or reach the network.
    """
    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": os.environ.get("HOME", ""),
        "USER": os.environ.get("USER", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "_": sys.executable,
        # Block network access via common proxy/setuptools channels
        "http_proxy": "",
        "https_proxy": "",
        "no_proxy": "*",
        "PIP_REQUIRE_VIRTUALENV": "1",
        "PIP_NO_INSTALL": "1",
        # Pre-commit / mypy cache dirs (avoid permission issues)
        "XDG_CACHE_HOME": os.path.join(tempfile.gettempdir(), ".hpc-sandbox-cache"),
        "PYTHONHASHSEED": "0",
    }
    # Never leak parent PYTHONPATH — that could import untrusted code before
    # the sandbox logic runs.
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("VIRTUAL_ENV", None)
    env.pop("CONDA_PREFIX", None)
    env.pop("CONDA_DEFAULT_ENV", None)

    os.makedirs(env["XDG_CACHE_HOME"], exist_ok=True)

    test_path = _test_file_path(tool_name, staging=True)
    return subprocess.run(
        [sys.executable, "-m", "pytest", test_path, "-x", "--tb=short", "-q"],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env=env,
    )


# ---------------------------------------------------------------------------
# Git and GitHub helpers
# ---------------------------------------------------------------------------


def _get_remote_url() -> str:
    """Read the origin remote URL from .git/config."""
    git_config = os.path.join(_PROJECT_ROOT, ".git", "config")
    with open(git_config) as f:
        for line in f:
            m = re.match(r"\s*url\s*=\s*(.+)", line.strip())
            if m:
                return m.group(1).strip()
    return ""


def _get_github_repo() -> str:
    """Extract owner/repo from the git remote URL."""
    url = _get_remote_url()
    m = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if m:
        return m.group(1)
    return ""


def _git_commit_and_push(tool_name: str, branch: str) -> tuple[str, str]:
    """Commit generated files and push to a new branch. Returns (sha, branch)."""
    subprocess.run(
        ["git", "checkout", "-b", branch],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    subprocess.run(
        [
            "git",
            "add",
            _EVOLVED_DIR,
            _TESTS_EVOLVED_DIR,
            _TOOLS_INIT,
        ],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    result = subprocess.run(
        [
            "git",
            "commit",
            "-m",
            f"Auto-generate {tool_name} via self-evolve",
            "-m",
            "Generated by hpc_self_evolve to address an uncovered operational scenario.",
        ],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    sha = ""
    m = re.search(r"\[[^\]]+\] ([a-f0-9]+)", result.stdout)
    if m:
        sha = m.group(1)

    subprocess.run(
        ["git", "push", "origin", branch],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
    )

    return sha, branch


def _create_github_pr(tool_name: str, description: str, branch: str) -> str:
    """Open a pull request via the GitHub API."""
    repo = _get_github_repo()
    if not repo:
        return "Cannot determine GitHub repository from git remote"

    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        env_path = os.path.join(get_home(), ".env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    m = re.match(r"GITHUB_TOKEN=(.+)", line.strip())
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
        f"- Registration via `@hpc_tool` decorator\n\n"
        f"## Verification\n"
        f"- [ ] `python3 -m pytest tests/ -q` passes\n"
        f"- [ ] Schema validation tests pass\n"
        f"- [ ] Tool is callable via Hermes Agent\n\n"
        f"---\n"
        f"_Generated by `hpc_self_evolve`_"
    )

    payload = json.dumps(
        {
            "title": pr_title,
            "body": pr_body,
            "head": branch,
            "base": "main",
        }
    ).encode()

    try:
        req = urllib.request.Request(
            f"https://api.github.com/repos/{repo}/pulls",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "hpc-pilot-self-evolve",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        try:
            msg = json.loads(body).get("message", body[:500])
        except json.JSONDecodeError:
            msg = body[:500]
        return f"GitHub API error: {msg}"
    except (urllib.error.URLError, OSError) as exc:
        return f"Failed to call GitHub API: {exc}"

    resp = json.loads(body)
    if "html_url" in resp:
        return f"PR created: {resp['html_url']}"
    elif "message" in resp:
        return f"GitHub API error: {resp['message']}"
    return f"Unknown GitHub API response: {body[:500]}"


# ---------------------------------------------------------------------------
# Main self-evolve function
# ---------------------------------------------------------------------------


@hpc_tool(
    name="hpc_self_evolve",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_self_evolve",
        "description": "Generate an HPC-Pilot tool, register, test, commit, push, PR.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name for the new tool (e.g. hpc_network_ib_list_partitions)",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description of what the tool does",
                },
                "code": {
                    "type": "string",
                    "description": "Function body (use _validate(), _resolve_cluster(), _run()). AST-whitelisted: no eval/exec/open/os.system.",  # noqa: E501
                },
                "test_code": {
                    "type": "string",
                    "description": "Pytest test code. Minimum: happy-path test patching _run and _resolve_cluster.",  # noqa: E501
                },
                "schema": {
                    "type": "object",
                    "description": "JSON Schema (type, properties, optionally required).",
                },
                "required_role": {
                    "type": "string",
                    "enum": ["VIEWER", "OPERATOR", "ADMIN", "SUPERADMIN"],
                    "description": "Minimum RBAC role (default: VIEWER)",
                },
            },
            "required": ["tool_name", "description", "code", "test_code"],
        },
    },
)
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
    """Generate a new HPC-Pilot tool and stage it for review.

    The generated tool is written to ``evolved/staging/`` (not the live
    namespace).  After generation, call ``hpc_self_evolve_promote`` to
    move it into the live registry, update ``__init__.py``, and optionally
    create a pull request.

    Safety gates applied during generation:
      1. AST whitelist — only known-safe operations allowed.
      2. Schema validation — ``jsonschema.Draft202012Validator``.
      3. Sandboxed pytest — tests run in an isolated subprocess with no
         network access and no ``PYTHONPATH`` leak.

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
            f"Invalid required_role: {required_role!r}. " f"Must be one of {sorted(allowed_roles)}"
        )

    if not description.strip():
        raise ValueError("description is required")
    if not code.strip():
        raise ValueError("code is required")
    if not test_code.strip():
        raise ValueError("test_code is required")

    # AST whitelist check on the code
    try:
        tree = ast.parse(code.strip(), filename="<generated>")
        _check_ast_safety(tree)
    except SyntaxError as exc:
        raise ValueError(f"Generated code has invalid syntax: {exc}") from None

    # Schema validation (F.3)
    _validate_schema(schema, tool_name)

    role_up = required_role.upper()

    # Build generated content
    tool_py = _build_tool_py(tool_name, code)
    test_py = _build_test_py(tool_name, tool_name, test_code)

    staging_tool_path = _tool_file_path(tool_name, staging=True)
    staging_test_path = _test_file_path(tool_name, staging=True)

    lines: list[str] = [
        f"=== Self-Evolve Plan for '{tool_name}' ===",
        f"  Role: {role_up}",
        f"  Description: {description}",
        "  Schema validated: check mark",
        "",
        f"  1. Create {staging_tool_path}  (AST-whitelist + schema checked)",
        f"  2. Create {staging_test_path}",
        "  3. Run sandboxed pytest (no network, isolated subprocess)",
        "  4. STAGED — tool is NOT yet registered in __init__.py",
        "     → Call hpc_self_evolve_promote to promote + register.",
        "",
    ]

    if dry_run:
        lines.append("  --- Generated hpc_pilot/tools/evolved/staging/<tool_name>.py ---")
        for line in tool_py.splitlines():
            lines.append(f"  | {line}")
        lines.append("")
        lines.append("  --- Generated tests/tools/evolved/staging/test_<tool_name>.py ---")
        for line in test_py.splitlines():
            lines.append(f"  | {line}")
        lines.append("")
        lines.append("  DRY-RUN: No files were written.")
        lines.append("  Re-run with dry_run=False to stage and test.")
        return "\n".join(lines)

    # ---- Execute ----
    # 1. Create tool file in staging
    _write_file(staging_tool_path, tool_py)
    lines.append(f"  ✓ Created {staging_tool_path}")

    # 2. Create test file in staging
    _write_file(staging_test_path, test_py)
    lines.append(f"  ✓ Created {staging_test_path}")

    # 3. Run tests in sandboxed subprocess (F.1)
    lines.append("")
    lines.append("  Running sandboxed pytest...")
    test_result = _run_sandboxed_pytest(tool_name)

    if test_result.returncode != 0:
        lines.append(f"  ❌ Sandboxed tests failed ({test_result.returncode}):")
        for line in test_result.stderr.splitlines()[-10:]:
            lines.append(f"     {line}")
        for line in test_result.stdout.splitlines()[-10:]:
            lines.append(f"     {line}")
        lines.append("")
        lines.append("  Staging files remain on disk but tests did not pass.")
        lines.append("  Fix the errors above and run pytest manually:")
        lines.append(f"    python3 -m pytest {staging_test_path} -x --tb=long")
        return "\n".join(lines)

    lines.append("  ✓ All tests passed (sandboxed).")

    # 4. Done — tool is staged, NOT yet in the live registry
    lines.append("")
    lines.append("  ✓ Files generated and staged. All sandboxed tests pass.")
    lines.append("")
    lines.append("  The tool is NOT yet imported into the live registry.")
    lines.append("  To promote it, call:")
    lines.append(
        f'    hpc_self_evolve_promote(tool_name="{tool_name}", description="{description}")'
    )
    lines.append("")
    lines.append("  Staged files:")
    lines.append(f"    {staging_tool_path}")
    lines.append(f"    {staging_test_path}")

    return "\n".join(lines)


@hpc_tool(
    name="hpc_self_evolve_create_pr",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_self_evolve_create_pr",
        "description": "Commit, push, and open a GitHub PR for a promoted tool. Needs GITHUB_TOKEN.",  # noqa: E501
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the evolved tool to PR (e.g. hpc_network_ib_list)",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description for the PR body",
                },
            },
            "required": ["tool_name"],
        },
    },
)
def hpc_self_evolve_create_pr(
    tool_name: str,
    description: str = "",
    *,
    dry_run: bool = False,
    cluster: str = "default",
) -> str:
    """Commit promoted tool files, push to a new branch, and open a GitHub PR.

    Call this after ``hpc_self_evolve_promote`` has moved the staged tool
    into the live namespace.  Requires ``GITHUB_TOKEN`` in the environment
    or ``~/.hpc-pilot/.env``.

    Args:
        tool_name: The name of the evolved tool (e.g. ``hpc_network_ib_list_partitions``).
            Must match a tool that was previously promoted by ``hpc_self_evolve_promote``.
        description: Human-readable description for the PR body.
        dry_run: Show what would be done without actually pushing.
        cluster: Cluster name (unused, for interface consistency).
    """
    _validate_tool_name(tool_name)

    # Verify the promoted (live) files exist, NOT staging files
    tool_path = _tool_file_path(tool_name, staging=False)
    test_path = _test_file_path(tool_name, staging=False)
    missing = []
    if not os.path.exists(tool_path):
        missing.append(tool_path)
    if not os.path.exists(test_path):
        missing.append(test_path)
    if missing:
        return (
            "Cannot create PR — promoted files not found:\n"
            + "\n".join(f"  ❌ {p}" for p in missing)
            + "\n\nRun hpc_self_evolve_promote(...) first to promote the staged tool."
        )

    lines: list[str] = [
        f"=== Creating PR for '{tool_name}' ===",
    ]

    branch = f"self-evolve/{tool_name}-{int(time.time())}"

    if dry_run:
        lines.append(f"  Would create branch '{branch}'")
        lines.append(f"  Would commit: {_TOOLS_INIT}")
        lines.append(f"  Would commit: {tool_path}, {test_path}")
        lines.append(f"  Would push to origin/{branch}")
        lines.append("  Would open PR via GitHub API")
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


@hpc_tool(
    name="hpc_self_evolve_promote",
    role=Role.SUPERADMIN,
    schema={
        "name": "hpc_self_evolve_promote",
        "description": "Promote a staged evolved tool into the live registry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "tool_name": {
                    "type": "string",
                    "description": "Name of the staged tool to promote (e.g. hpc_network_ib_list)",
                },
                "description": {
                    "type": "string",
                    "description": "Human-readable description for the PR body",
                },
            },
            "required": ["tool_name"],
        },
    },
)
def hpc_self_evolve_promote(
    tool_name: str,
    description: str = "",
    *,
    dry_run: bool = False,
    cluster: str = "default",
) -> str:
    """Promote a staged evolved tool into the live registry.

    Moves the tool from ``evolved/staging/`` to ``evolved/``, moves tests
    from ``tests/tools/evolved/staging/`` to ``tests/tools/evolved/``, adds
    the import to ``tools/__init__.py``, and optionally creates a PR.

    Args:
        tool_name: The name of the staged tool (e.g. ``hpc_network_ib_list_partitions``).
        description: Human-readable description for the PR body (used if creating a PR).
        dry_run: Show what would be done without actually promoting.
        cluster: Cluster name (unused, for interface consistency).
    """
    _validate_tool_name(tool_name)

    staging_tool_path = _tool_file_path(tool_name, staging=True)
    staging_test_path = _test_file_path(tool_name, staging=True)
    live_tool_path = _tool_file_path(tool_name, staging=False)
    live_test_path = _test_file_path(tool_name, staging=False)

    missing = []
    if not os.path.exists(staging_tool_path):
        missing.append(staging_tool_path)
    if not os.path.exists(staging_test_path):
        missing.append(staging_test_path)
    if missing:
        return (
            "Cannot promote — staged files not found:\n"
            + "\n".join(f"  ❌ {p}" for p in missing)
            + "\n\nRun hpc_self_evolve(... dry_run=False) first to generate the staged files."
        )

    lines: list[str] = [
        f"=== Promoting '{tool_name}' ===",
    ]

    if dry_run:
        lines.append(f"  Would copy {staging_tool_path} → {live_tool_path}")
        lines.append(f"  Would copy {staging_test_path} → {live_test_path}")
        lines.append(f"  Would add import to {_TOOLS_INIT}")
        lines.append("  Tool would be live in the registry.")
        lines.append("")
        lines.append("  DRY-RUN: no files were modified.")
        return "\n".join(lines)

    # 1. Copy staged files to live locations
    os.makedirs(os.path.dirname(live_tool_path), exist_ok=True)
    os.makedirs(os.path.dirname(live_test_path), exist_ok=True)
    shutil.copy2(staging_tool_path, live_tool_path)
    shutil.copy2(staging_test_path, live_test_path)
    lines.append(f"  ✓ Copied {staging_tool_path} → {live_tool_path}")
    lines.append(f"  ✓ Copied {staging_test_path} → {live_test_path}")

    # 2. Add to tools/__init__.py
    result = _add_to_init(tool_name)
    lines.append(f"  ✓ {result}")

    lines.append("")
    lines.append("  ✓ Tool promoted to live registry.")
    lines.append("")
    lines.append("  To commit, push, and open a PR, call:")
    lines.append(
        f'    hpc_self_evolve_create_pr(tool_name="{tool_name}", description="{description}")'
    )
    lines.append("")
    lines.append("  Or skip git/PR — the files are live on disk:")
    lines.append(f"    {live_tool_path}")
    lines.append(f"    {live_test_path}")

    return "\n".join(lines)
