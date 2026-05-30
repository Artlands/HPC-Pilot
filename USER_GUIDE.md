# HPC Pilot User Guide

HPC Pilot helps cluster operators preview, approve, apply, and audit changes across Slurm,
Warewulf, Ansible, and Spack. This guide focuses on practical operation and extension of
the current tool.

## Operating Principles

- Mutating tools dry-run by default.
- Every mutation produces a structured diff before execution.
- Medium and high-risk operations are gated by RBAC, policy, and blast radius.
- All shell execution goes through an allowlisted runner.
- Applied operations can be stored in a durable audit database.
- Desired cluster state is stored separately from audit history.

## Interfaces

### One-Shot CLI

```bash
hpc-pilot <command> [options]
```

Use this for scripts and explicit operations.

### Interactive Shell

```bash
hpc-pilot shell
```

The shell accepts natural-language intents and slash commands:

```text
give alice 48 hours of wall time on the gpu qos
/run
/approve
/show
/tools
/help
/exit
```

### Terminal UI

```bash
hpc-pilot tui
```

The TUI shows a conversation pane, current plan pane, status bar, and input bar. Use the
same slash commands as the shell. Arrow up/down scroll the transcript; page up/down scroll
the plan pane.

## Installation and Configuration

```bash
pip install -e ".[dev]"
```

Local development configuration:

```bash
export HPC_DB_URL=sqlite+pysqlite:////tmp/hpc-pilot-state.sqlite
export HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-pilot-audit.sqlite
export HPC_CONFIG_REPO="$PWD/config_repo"
alembic upgrade head
hpc-pilot audit-init
```

Production deployments should set PostgreSQL-compatible `HPC_DB_URL` and
`HPC_AUDIT_DB_URL` values and point `HPC_CONFIG_REPO` and `HPC_ANSIBLE_DIR` at managed
site directories.

## Configuration Reference

| Variable | Description |
|----------|-------------|
| `HPC_DB_URL` | Desired-state database URL |
| `HPC_AUDIT_DB_URL` | Durable audit database URL |
| `HPC_AUDIT_SINK` | `memory` or `db` |
| `HPC_AUDIT_AUTO_INIT` | Create audit tables automatically for DB sink |
| `HPC_CONFIG_REPO` | Git repository for managed configuration |
| `HPC_SLURM_BIN_DIR` | Directory containing Slurm CLIs |
| `HPC_WW_BIN_DIR` | Directory containing `wwctl` |
| `HPC_SPACK_ROOT` | Spack root directory |
| `HPC_ANSIBLE_DIR` | Ansible roles, playbooks, inventory, and secret refs |
| `HPC_APPROVAL_BACKEND` | `cli`, `api`, or `mock` |
| `HPC_MAX_BLAST_RADIUS_AUTO` | Maximum blast radius for automatic execution |

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

- `audit_events`: actor, tool, risk, input, decision, result status, diff summary,
  config commit, and revert argv.
- `audit_commands`: redacted argv, return code, and command duration for each event.

## Slurm Operations

### QOS

```bash
# Preview wall-time change
hpc-pilot qos gpu --op modify --max-wall-min 2880

# Apply after review
hpc-pilot qos gpu --op modify --max-wall-min 2880 --apply

# Create a QOS
hpc-pilot qos debug --op create --max-wall-min 60 --apply
```

### Accounts and Associations

```bash
hpc-pilot account research --op modify --grp-tres cpu=512
hpc-pilot assoc alice research --qos-add gpu
hpc-pilot set-limits qos --name gpu --max-jobs-pu 8
```

### Nodes, Reservations, and Diagnostics

```bash
hpc-pilot node-status --node gpu01
hpc-pilot node-state gpu01 drain --reason maintenance
hpc-pilot node-state gpu01 drain --reason maintenance --apply
hpc-pilot node-state gpu01 resume --apply

hpc-pilot reservation maint-gpu create --nodes gpu01 --start 2026-06-01T01:00:00 --duration-min 60
hpc-pilot reconfigure
hpc-pilot diag
```

### Queries and Reports

```bash
hpc-pilot show-assoc --user alice --account research
hpc-pilot queue --user alice --partition gpu
hpc-pilot job-accounting --user alice --start 2026-05-01 --end 2026-05-31
hpc-pilot usage-report --account research --start 2026-05-01
```

## Ansible Operations

HPC Pilot assumes roles are curated and checked into the site Ansible directory. The agent
does not generate arbitrary freeform tasks.

```bash
hpc-pilot compose-playbook site compute_gpu --roles common --roles chrony
hpc-pilot manage-inventory
hpc-pilot lint-playbook /etc/hpc-pilot/ansible/playbooks/site.yml
hpc-pilot run-playbook /etc/hpc-pilot/ansible/playbooks/site.yml --limit gpu01
hpc-pilot check-secret munge/key
```

`run-playbook` performs lint and syntax checks before apply and uses Ansible check/diff
for dry-run previews.

## Spack Operations

```bash
hpc-pilot spack-envs
hpc-pilot spack-find gpu-stack
hpc-pilot spack-spec "openmpi@5 +cuda"

hpc-pilot spack-compilers --op find --scope site
hpc-pilot spack-compilers --op add --scope site --path /opt/gcc/bin

hpc-pilot spack-env my-env --op create
hpc-pilot spack-env my-env --op add_specs --specs "gcc@13" --specs "openmpi"
hpc-pilot spack-buildcache push /path/to/mirror
hpc-pilot spack-modules gpu-stack --module-type lmod
hpc-pilot spack-view gpu-stack --prefix /opt/modules
hpc-pilot spack-install gpu-stack
```

## Warewulf Operations

The Warewulf tool surface is registered for planner/tool use. Some operations may be
more practical from Python or future workflow commands than from the current one-shot CLI.
Use `hpc-pilot tools` to inspect registered schemas.

Registered Warewulf tools include:

- `warewulf.import_container`
- `warewulf.build_node_image`
- `warewulf.define_profile`
- `warewulf.manage_overlay`
- `warewulf.assign_image_to_nodes`
- `warewulf.provision_node`
- `warewulf.rebuild_overlay`
- `warewulf.query.list_images`
- `warewulf.query.list_nodes`

## Plans and Approvals

Plan an operation:

```bash
hpc-pilot plan "give alice 48 hours of wall time on the gpu qos"
```

Execute it:

```bash
hpc-pilot plan "give alice 48 hours of wall time on the gpu qos" --apply
```

If a step requires approval, the plan pauses. In the shell or TUI, use `/approve` to
resume after reviewing the pending diff.

## Policies

Policies are YAML files in `config_repo/policy/`. They can deny actions, require
approval, or explicitly allow safe medium-risk actions. Example policy ideas:

- Deny QOS wall time above a site cap.
- Require approval for high blast-radius node operations.
- Deny medium/high-risk mutations during maintenance blackout windows.

Keep production policy files in version control and review them like operational code.

## RBAC Roles

| Role | Intended Use |
|------|--------------|
| `viewer` | Read-only queries and reports |
| `operator` | Day-to-day managed operations within policy |
| `admin` | Full tool access |

RBAC is enforced before policy approval decisions.

## Developer Workflow

Run checks:

```bash
ruff check .
black --check .
mypy hpc_agent tests
pytest tests/unit
```

Add a tool:

1. Define a Pydantic input model.
2. Register the function with `@tool`.
3. Read current state and compute a delta.
4. Return no-op success when already converged.
5. Build a structured `Diff`.
6. Evaluate the safety gate.
7. Execute only through `run_command`.
8. Commit an audit event.
9. Add unit tests around dry-run, apply, denial, no-op, and failures.

## Troubleshooting

**Command failed because the site CLI is missing**

Install or configure the relevant Slurm, Warewulf, Spack, or Ansible binary. For local
development, mock `run_command` in tests.

**Audit events are not durable**

Set `HPC_AUDIT_SINK=db`, initialize the audit DB with `hpc-pilot audit-init`, and confirm
`HPC_AUDIT_DB_URL` points to the intended database.

**Policy denies an expected operation**

Inspect files in `config_repo/policy/`, then rerun without `--apply` to review the diff
and policy result.

**The natural-language planner cannot parse an intent**

Use the explicit CLI command for now. The current rule-based planner handles common QOS
wall-time intents; broader LLM planning is a separate integration point.
