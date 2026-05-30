# HPC Pilot

HPC Pilot is an operator-focused agent for managing HPC clusters that use Slurm,
Warewulf, Ansible, and Spack. It provides typed tools, dry-run diffs, policy gates,
audit logging, and interactive command-line interfaces for common cluster operations.

The project is designed around a simple rule: every operation should be previewable,
auditable, and reversible where the underlying system allows it.

## Key Features

- Slurm operations for QOS, accounts, user associations, node state, reservations,
  queue/accounting queries, diagnostics, and controller reconfiguration.
- Ansible helpers for curated playbook composition, inventory generation, linting,
  dry-run/apply workflows, and secret-reference checks.
- Spack helpers for environment queries, compiler discovery, environment edits,
  buildcache management, module generation, views, and installs.
- Warewulf tool surface for container import, image builds, profiles, overlays,
  node provisioning, image assignment, and overlay rebuilds.
- Safety layer with structured diffs, RBAC, YAML policies, blast-radius checks,
  dry-run defaults, approval pauses, and resumable plans.
- Durable operation tracking with SQL-backed `audit_events` and `audit_commands`
  tables.
- CLI, REPL shell, and split-pane terminal UI.

## Install

```bash
pip install -e ".[dev]"
```

Or with LLM support (choose one):

```bash
# Anthropic Claude
pip install -e ".[dev,anthropic]"

# OpenAI ChatGPT
pip install -e ".[dev,openai]"
```

For local development, SQLite is the easiest way to try the state and audit stores:

```bash
export HPC_DB_URL=sqlite+pysqlite:////tmp/hpc-pilot-state.sqlite
export HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-pilot-audit.sqlite
export HPC_CONFIG_REPO="$PWD/config_repo"

alembic upgrade head
hpc-pilot audit-init
```

Production deployments should use PostgreSQL-compatible URLs for `HPC_DB_URL` and
`HPC_AUDIT_DB_URL`.

## LLM Configuration

HPC Pilot supports natural-language plan generation using LLMs. Configure your provider:

### Anthropic Claude (default)

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=your-api-key
```

### OpenAI ChatGPT

```bash
export LLM_PROVIDER=openai
export OPENAI_API_KEY=your-api-key
```

### Custom local LLM (e.g., Ollama)

Implement the `LLMProvider` interface (see `hpc_agent/core/llm.py`) and register it in
`get_llm_provider()`. Then set `LLM_PROVIDER` to your custom provider name.

### LLM-DISABLED mode

Set `LLM_PROVIDER=mock` to disable LLM calls (useful for testing or CLI-only operation).

Plan from natural language intent requires LLM. Without LLM, use rule-based planning via
`hpc-pilot plan --apply` with supported intent patterns, or use direct tool commands.

## Interfaces

List available commands:

```bash
hpc-pilot --help
```

Start the interactive REPL:

```bash
hpc-pilot shell
```

Start the split-pane terminal UI:

```bash
hpc-pilot tui
```

Useful shell/TUI commands:

```text
<intent>          build and display a plan
/run [intent]     execute the current plan, or build and execute a new intent
/approve [step]   approve and resume a paused plan step
/show             show the current plan
/tools            list registered tools
/help             show interactive help
/exit             quit
```

## Common Operations

Dry-run is the default for mutating tools.

```bash
# Preview a QOS change
hpc-pilot qos gpu --op modify --max-wall-min 2880

# Apply after reviewing the diff and policy result
hpc-pilot qos gpu --op modify --max-wall-min 2880 --apply

# Inspect Slurm state
hpc-pilot node-status --node gpu01
hpc-pilot queue --user alice --partition gpu
hpc-pilot usage-report --account research --start 2026-05-01

# Manage user/account associations
hpc-pilot assoc alice research --qos-add gpu
hpc-pilot assoc alice research --qos-add gpu --apply

# Drain and resume a node
hpc-pilot node-state gpu01 drain --reason maintenance
hpc-pilot node-state gpu01 drain --reason maintenance --apply
hpc-pilot node-state gpu01 resume --apply
```

Plan from a natural-language intent (requires LLM):

```bash
hpc-pilot plan "give alice 48 hours of wall time on the gpu qos"
hpc-pilot plan "give alice 48 hours of wall time on the gpu qos" --apply
```

Track applied operations:

```bash
export HPC_AUDIT_SINK=db
hpc-pilot audit-log --result-status ok
hpc-pilot audit-show <audit_id>
```

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `HPC_DB_URL` | Desired-state database URL | `postgresql+psycopg://hpcagent@localhost/hpc_agent` |
| `HPC_AUDIT_DB_URL` | Audit database URL | `postgresql+psycopg://hpcagent@localhost/hpc_audit` |
| `HPC_AUDIT_SINK` | Audit sink: `memory` or `db` | `memory` |
| `HPC_AUDIT_AUTO_INIT` | Auto-create audit tables for the DB sink | `false` |
| `HPC_CONFIG_REPO` | Git-backed config repository | `/etc/hpc-pilot/config` |
| `HPC_SLURM_BIN_DIR` | Slurm binary directory | `/usr/bin` |
| `HPC_WW_BIN_DIR` | Warewulf binary directory | `/usr/bin` |
| `HPC_SPACK_ROOT` | Spack root | `/opt/spack` |
| `HPC_ANSIBLE_DIR` | Ansible control directory | `/etc/hpc-pilot/ansible` |
| `HPC_APPROVAL_BACKEND` | Approval backend: `cli`, `api`, or `mock` | `cli` |
| `HPC_MAX_BLAST_RADIUS_AUTO` | Auto-run blast-radius cap | `4` |
| `LLM_PROVIDER` | LLM backend: `anthropic`, `openai`, `mock` | `anthropic` |
| `ANTHROPIC_API_KEY` | Anthropic API key | - |
| `OPENAI_API_KEY` | OpenAI API key | - |

### LLM Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `LLM_PROVIDER` | LLM backend: `anthropic`, `openai`, `mock` | `anthropic` |
| `ANTHROPIC_API_KEY` | Anthropic API key (if using Claude) | - |
| `OPENAI_API_KEY` | OpenAI API key (if using ChatGPT) | - |

Sample policy files live in [config_repo/policy](config_repo/policy).

## Safety Model

HPC Pilot tools follow the same operational contract:

1. Validate input with Pydantic.
2. Read current state from the live system and/or desired-state repository.
3. Compute the delta.
4. Return a structured no-op when the target is already converged.
5. Build a human-readable `Diff`.
6. Evaluate RBAC, policy, risk tier, and blast radius.
7. Honor dry-run and approval decisions before executing.
8. Execute only through the allowlisted command runner.
9. Record audit data, commands, revert hints, and state updates.

Medium and high-risk actions can pause for approval. Read-only and low-risk actions may
auto-run when policy allows them.

## Development

Run the standard checks:

```bash
ruff check .
black --check .
mypy hpc_agent tests
pytest tests/unit
```

Add a new tool by following the established pattern:

1. Define a Pydantic input model.
2. Register the function with `@tool`.
3. Build a `Diff` before mutation.
4. Gate with `safety_gate.evaluate`.
5. Execute through `run_command`.
6. Commit an audit event.
7. Add unit tests for dry-run, apply, policy denial, no-op, and command failures.

More detailed operator and developer documentation is available in:

- [Quick Start](QUICK_START.md)
- [User Guide](USER_GUIDE.md)
- [Documentation Index](DOCS_INDEX.md)
- [Design Reference](agent-specs/README.md)
