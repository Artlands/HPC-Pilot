# hpc-agent

AI agent that configures and manages an HPC cluster (Warewulf, Ansible, Slurm, Spack).

This repository is the **scaffold + reference implementation** for the specs in
`./agent-specs/`. It implements the foundations (spec 00), the safety layer
(spec 01), and one fully-worked reference tool — `slurm.manage_qos` (spec 05 §1.2) — that
every other tool should be patterned after.

## What's implemented

| Component | Spec | File |
|-----------|------|------|
| Settings | 00 §7 | `hpc_agent/config/settings.py` |
| Error taxonomy | 00 §3.3 | `hpc_agent/tools/errors.py` |
| ToolResult | 00 §3.2 | `hpc_agent/tools/result.py` |
| `@tool` registry + risk tiers | 00 §3.1 | `hpc_agent/tools/base.py` |
| Command executor (allowlist, redaction) | 00 §4 | `hpc_agent/exec/runner.py` |
| Audit log | 00 §5 | `hpc_agent/exec/audit.py` |
| RBAC | 00 §6 | `hpc_agent/exec/rbac.py` |
| Diff model | 01 §2 | `hpc_agent/safety/diff.py` |
| Policy engine (YAML) | 01 §4 | `hpc_agent/safety/policy.py` |
| Safety gate | 01 §3 | `hpc_agent/safety/gate.py` |
| **State store ORM** | 00 §1.1 | `hpc_agent/state/models.py` |
| **Repositories** | 00 §1.2 | `hpc_agent/state/repos.py` |
| **DB / session mgmt** | 00 §1 | `hpc_agent/state/db.py` |
| **Plan / Step models** | 02 §3 | `hpc_agent/core/plan.py` |
| **Topological ordering** | 02 §4 | `hpc_agent/core/ordering.py` |
| **Planner (rule-based)** | 02 §2-3 | `hpc_agent/core/planner.py` |
| **Executor (+ resume)** | 02 §4-5 | `hpc_agent/core/executor.py` |
| **Plan store** | 02 §5 | `hpc_agent/core/planstore.py` |
| **Reference tool: manage_qos** | 05 §1.2 | `hpc_agent/tools/slurm.py` |
| CLI (`tools`/`qos`/`plan`) | 02 §7 | `hpc_agent/core/interaction.py` |

Sample policy lives in `config_repo/policy/`.

## Quickstart

```bash
pip install -e ".[dev]"

# list registered tools + JSON schemas (for LLM tool-calling)
hpc-agent tools

# dry-run a QOS wall-time extension (needs a sacctmgr on PATH; see tests for stubbing)
HPC_CONFIG_REPO=$PWD/config_repo hpc-agent qos gpu --op modify --max-wall-min 2880
# add --apply to actually execute (gated by policy)

# build a plan from a natural-language intent, then optionally execute it
hpc-agent plan "give alice 48 hours of wall time on the gpu qos"
hpc-agent plan "extend the normal qos wall time to 2 days" --apply
```

## Quality gates (all green)

```bash
ruff check hpc_agent/        # lint
mypy hpc_agent/              # strict type check (29 files)
pytest                       # 36 unit tests
```

> Note: SQLAlchemy 2.0 ships its own typing and a mypy plugin (configured in
> `pyproject.toml`). Do **not** install `sqlalchemy-stubs` / `sqlalchemy2-stubs` — they
> conflict with the 2.0 plugin.

## How to add the next tool

Copy the structure of `manage_qos` in `hpc_agent/tools/slurm.py`. Every mutating tool
follows the spec 00 §3.4 execution contract in order:

1. Define a Pydantic `*In` model; decorate the function with `@tool(name=..., risk=...,
   domain=..., blast_radius=...)`.
2. Open an audit event.
3. Read current state (via `run_command` with `-P`/`--json`; parse structurally).
4. Compute the delta; return an idempotent no-op if there's nothing to change.
5. Build a `Diff` (changes + redacted command preview + blast radius + reversibility).
6. Call `safety_gate.evaluate(...)`.
7. Honor `dry_run`, `denied`, and `needs_approval` before executing.
8. Snapshot config (if the tool edits config files) before mutating.
9. Execute via `run_command`.
10. Record the inverse command(s) for revert, commit the audit event, upsert state.
11. Return a `ToolResult`.

Tests for a new tool should mock `run_command` (see `tests/unit/test_manage_qos.py`) and
cover: dry-run mutates nothing, in-policy auto-apply, out-of-policy deny, idempotent
no-op, not-found precondition, and inverse-command recording.

## Now implemented since the initial scaffold

- State store ORM + repositories (spec 00 §1); `manage_qos` upserts its desired-state row.
- Plan/Step models, topological ordering, rule-based planner, and the executor with
  pause-for-approval and diff-revalidated resume (spec 02 §3-5).

## Not yet implemented (next steps, per specs)

- Alembic migrations for the state schema (production; `init_db` covers tests/bootstrap).
- Remaining Slurm tools, plus Warewulf / Ansible / Spack tools (specs 03–06).
- The LLM planner (`core/llm.py`) implementing `build_plan` for open-ended intents; the
  rule-based planner is the deterministic stand-in.
- Config-repo git wrapper + rollback (spec 00 §2, 01 §5); approval backends beyond CLI.
- Virtual cluster + integration/eval suites (spec 08).
