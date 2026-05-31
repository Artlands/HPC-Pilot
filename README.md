# HPC Pilot

HPC Pilot is an operator-focused agent for managing HPC clusters that use Slurm,
Warewulf, Ansible, and Spack. It provides typed tools, dry-run diffs, policy gates,
audit logging, and interactive command-line interfaces for common cluster operations.

Every operation is previewable, auditable, and reversible where the underlying system
allows it.

## Quick Start

```bash
pip install -e ".[dev]"

export HPC_DB_URL=sqlite+pysqlite:////tmp/hpc-pilot-state.sqlite
export HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-pilot-audit.sqlite
export HPC_CONFIG_REPO="$PWD/config_repo"

alembic upgrade head
hpc-pilot audit-init
```

For LLM-powered natural-language planning, install with an extra provider:

```bash
pip install -e ".[dev,anthropic]"   # or .[dev,openai]
```

## Interfaces

```bash
hpc-pilot --help          # list all commands
hpc-pilot shell           # interactive REPL
hpc-pilot tui             # split-pane terminal UI
```

### Shell / TUI commands

```text
<intent>          build and display a plan
/run [intent]     execute the current plan, or build and execute a new intent
/approve [step]   approve and resume a paused plan step
/show             show the current plan
/tools            list registered tools
/help             show interactive help
/exit             quit
```

## Installation

```bash
pip install -e ".[dev]"
```

Optional LLM support:

```bash
pip install -e ".[dev,anthropic]"   # Anthropic Claude
pip install -e ".[dev,openai]"      # OpenAI ChatGPT
```

Production deployments should use PostgreSQL-compatible URLs for `HPC_DB_URL` and
`HPC_AUDIT_DB_URL`.

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
| `HPC_DRY_RUN_DEFAULT` | Dry-run by default for mutating tools | `true` |

Sample policy files live in [config_repo/policy](config_repo/policy).

## LLM Configuration

HPC Pilot supports natural-language plan generation using LLMs.

### Supported providers

| Provider | Model Family | API Key Env Var |
|----------|-------------|-----------------|
| Anthropic | Claude 3.x | `ANTHROPIC_API_KEY` |
| OpenAI | GPT-4o, GPT-4 Turbo | `OPENAI_API_KEY` |

### Setup

```bash
# Anthropic Claude (default)
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-api03-...

# OpenAI ChatGPT
export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-proj-...

# Disable LLM (CLI-only / testing)
export LLM_PROVIDER=mock
```

Switch providers per-command:

```bash
LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=... hpc-pilot plan "install 4 GPU nodes"
LLM_PROVIDER=openai OPENAI_API_KEY=... hpc-pilot plan "extend user wall time"
```

### How it works

1. **Intent** — LLM receives your natural language request.
2. **Tool schemas** — LLM sees available HPC Pilot tools.
3. **Plan** — LLM generates ordered tool calls with arguments.
4. **Safety** — All plans go through RBAC, policy, and approval gates.
5. **Execution** — Plan steps execute with dry-run, approval, and apply.

### Custom provider

Implement `LLMProvider` in `hpc_agent/core/llm.py` and register it in
`get_llm_provider()`. Set `LLM_PROVIDER` to your custom provider name.

### Cost estimates

| Action | Cost |
|--------|------|
| Small intent | $0.001–$0.005 |
| Complex plan (10+ steps) | $0.01–$0.05 |

## Common Operations

Dry-run is the default for mutating tools.

```bash
# QOS changes
hpc-pilot qos gpu --op modify --max-wall-min 2880          # preview
hpc-pilot qos gpu --op modify --max-wall-min 2880 --apply  # apply

# Slurm queries
hpc-pilot node-status --node gpu01
hpc-pilot queue --user alice --partition gpu
hpc-pilot usage-report --account research --start 2026-05-01
hpc-pilot show-assoc --user alice --account research
hpc-pilot job-accounting --user alice --start 2026-05-01

# User/account associations
hpc-pilot assoc alice research --qos-add gpu
hpc-pilot assoc alice research --qos-add gpu --apply
hpc-pilot set-limits qos --name gpu --max-jobs-pu 8

# Node state
hpc-pilot node-state gpu01 drain --reason maintenance
hpc-pilot node-state gpu01 drain --reason maintenance --apply
hpc-pilot node-state gpu01 resume --apply

# Reservations
hpc-pilot reservation maint-gpu create --nodes gpu01 \
  --start 2026-06-01T01:00:00 --duration-min 60

# Diagnostics
hpc-pilot diag
hpc-pilot reconfigure
```

### Ansible

```bash
hpc-pilot compose-playbook site compute_gpu --roles common --roles chrony
hpc-pilot manage-inventory
hpc-pilot lint-playbook /etc/hpc-pilot/ansible/playbooks/site.yml
hpc-pilot run-playbook /etc/hpc-pilot/ansible/playbooks/site.yml --limit gpu01
hpc-pilot check-secret munge/key
```

### Spack

```bash
hpc-pilot spack-envs
hpc-pilot spack-find gpu-stack
hpc-pilot spack-spec "openmpi@5 +cuda"
hpc-pilot spack-compilers --op find --scope site
hpc-pilot spack-env my-env --op create
hpc-pilot spack-env my-env --op add_specs --specs "gcc@13" --specs "openmpi"
hpc-pilot spack-buildcache push /path/to/mirror
hpc-pilot spack-modules gpu-stack --module-type lmod
hpc-pilot spack-install gpu-stack
```

### Natural-language planning

```bash
hpc-pilot plan "give alice 48 hours of wall time on the gpu qos"
hpc-pilot plan "give alice 48 hours of wall time on the gpu qos" --apply
```

## Audit Log

Initialize and enable durable operation tracking:

```bash
hpc-pilot audit-init
export HPC_AUDIT_SINK=db
```

List and inspect audit events:

```bash
hpc-pilot audit-log
hpc-pilot audit-log --actor cli-user --result-status ok
hpc-pilot audit-log --json
hpc-pilot audit-show <audit_id>
```

The audit database contains:

- **`audit_events`** — actor, tool, risk, input, decision, result status, diff summary,
  config commit, and revert argv.
- **`audit_commands`** — redacted argv, return code, and command duration for each event.

## Safety Model

HPC Pilot tools follow this operational contract:

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

## Policies

Policies are YAML files in `config_repo/policy/`. They can deny actions, require
approval, or explicitly allow safe medium-risk actions. Examples:

- Deny QOS wall time above a site cap.
- Require approval for high blast-radius node operations.
- Deny medium/high-risk mutations during maintenance blackout windows.

Keep production policy files in version control and review them like operational code.

## RBAC

| Role | Intended Use |
|------|--------------|
| `viewer` | Read-only queries and reports |
| `operator` | Day-to-day managed operations within policy |
| `admin` | Full tool access |

RBAC is enforced before policy approval decisions.

## Development

Run the standard checks:

```bash
ruff check .
black --check .
mypy hpc_agent tests
pytest tests/unit
```

### Adding a new tool

1. Define a Pydantic input model.
2. Register the function with `@tool`.
3. Build a `Diff` before mutation.
4. Gate with `safety_gate.evaluate`.
5. Execute through `run_command`.
6. Commit an audit event.
7. Add unit tests for dry-run, apply, policy denial, no-op, and command failures.

## Troubleshooting

**Command failed because the site CLI is missing** — Install or configure the relevant
Slurm, Warewulf, Spack, or Ansible binary. For local development, mock `run_command` in
tests.

**Audit events are not durable** — Set `HPC_AUDIT_SINK=db`, initialize with
`hpc-pilot audit-init`, and confirm `HPC_AUDIT_DB_URL` points to the intended database.

**Policy denies an expected operation** — Inspect files in `config_repo/policy/`, then
rerun without `--apply` to review the diff and policy result.

**LLM cannot parse an intent** — Use the explicit CLI command for now. The rule-based
planner handles common QOS wall-time intents; broader LLM planning requires a configured
provider.

**LLM returns content instead of tool calls** — Use models with strong tool-calling
support (Claude 3.5 Sonnet+, GPT-4o, GPT-4 Turbo).

## Documentation

| Document | Description |
|----------|-------------|
| [README.md](README.md) | This file — project overview, setup, usage, and reference |
| [deploy/README.md](deploy/README.md) | Virtual-cluster deployment and integration testing |
| [agent-specs/](agent-specs/) | Engineering design reference for core contracts and domain tools |

The `agent-specs/` directory contains product and engineering reference documents. They
are not release notes or progress trackers.
