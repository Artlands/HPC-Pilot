# HPC AI Agent — Implementation Specifications

This is the authoritative specification set for building an AI agent that configures and
manages an HPC cluster. It is written so that an autonomous coding agent (or a human
engineer) can implement each component directly from the spec, with minimal additional
design decisions.

## How to read these specs

Implement in the order below. Each spec is self-contained but assumes the foundations
(`00`) already exist.

| # | Spec | Implements |
|---|------|-----------|
| 00 | [foundations.md](specs/00-foundations.md) | State store, tool framework, RBAC, audit, config-as-code |
| 01 | [safety-and-governance.md](specs/01-safety-and-governance.md) | Dry-run, approval gates, policy engine, rollback |
| 02 | [agent-core.md](specs/02-agent-core.md) | Planner, reasoner, memory, interaction layer |
| 03 | [tool-warewulf.md](specs/03-tool-warewulf.md) | Provisioning / image builds (CPU + GPU) |
| 04 | [tool-ansible.md](specs/04-tool-ansible.md) | Configuration management |
| 05 | [tool-slurm.md](specs/05-tool-slurm.md) | Scheduler / account / QOS management |
| 06 | [tool-spack.md](specs/06-tool-spack.md) | System software |
| 07 | [workflows.md](specs/07-workflows.md) | Composite management workflows |
| 08 | [testing.md](specs/08-testing.md) | Virtual cluster, eval suite, CI |

## Global conventions (apply to every spec)

- **Language:** Python 3.11+. **Style:** `ruff` + `black`, full type hints, `mypy --strict`.
- **Every tool is a typed function** with a Pydantic input model and a Pydantic output
  model. No tool takes or returns freeform strings except where explicitly noted.
- **No raw shell from the LLM.** All shell execution goes through `run_command()`
  (spec 00 §4), which logs, sanitizes, and enforces an allowlist.
- **Every mutating tool** accepts `dry_run: bool = True` and returns a `Diff` object
  (spec 01 §2) when `dry_run=True`.
- **Every tool** returns a `ToolResult` (spec 00 §3.2) carrying `status`, `data`,
  `diff`, `audit_id`, and `error`.
- **Idempotency:** mutating tools must be safe to re-run. Check current state, compute
  delta, apply only the delta.
- **Naming:** snake_case for code, kebab-case for files, UPPER_SNAKE for env vars.
- **Errors never crash the agent.** Tools catch, classify (see spec 00 §3.3), and return
  a structured error.

## Repository layout (target)

```
AutoHPC/
├── pyproject.toml
├── agent-specs/         # folder containing all the specs
├── hpc_agent/
│   ├── core/            # planner, memory, interaction (spec 02)
│   ├── state/           # ORM models, repositories (spec 00)
│   ├── exec/            # run_command, audit, rbac (spec 00)
│   ├── safety/          # diff, approval, policy, rollback (spec 01)
│   ├── tools/
│   │   ├── warewulf.py  # spec 03
│   │   ├── ansible.py   # spec 04
│   │   ├── slurm.py     # spec 05
│   │   └── spack.py     # spec 06
│   ├── workflows/       # spec 07
│   └── config/          # site policy, settings
├── roles/               # curated Ansible role library (spec 04)
├── tests/               # spec 08
└── deploy/              # virtual cluster definitions (spec 08)
```
