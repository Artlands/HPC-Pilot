# AutoHPC Quick Start

This guide gets a local development checkout running with SQLite-backed state and audit
databases. It is meant for trying the CLI safely, writing tests, and exploring the tool
contracts.

## Install

```bash
pip install -e ".[dev]"
```

## Configure Local State

```bash
export HPC_DB_URL=sqlite+pysqlite:////tmp/hpc-agent-state.sqlite
export HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-agent-audit.sqlite
export HPC_CONFIG_REPO="$PWD/config_repo"
```

Create the database schema:

```bash
alembic upgrade head
hpc-agent audit-init
```

## Inspect the CLI

```bash
hpc-agent --help
hpc-agent tools
```

AutoHPC also has interactive interfaces:

```bash
hpc-agent shell
hpc-agent tui
```

Inside either interface, try:

```text
give alice 48 hours of wall time on the gpu qos
/show
/run
/help
```

## Run a Dry-Run Operation

Most mutating commands default to dry-run. The command below previews a QOS update and
does not apply it:

```bash
hpc-agent qos gpu --op modify --max-wall-min 2880
```

Use `--apply` only when you want the agent to execute the live command:

```bash
hpc-agent qos gpu --op modify --max-wall-min 2880 --apply
```

Local machines usually do not have Slurm, Warewulf, Spack, or Ansible configured, so live
commands may fail unless those tools are installed or mocked. The unit tests show how the
external command runner is stubbed.

## Track Applied Operations

Enable durable audit logging for commands you want to track:

```bash
export HPC_AUDIT_SINK=db
hpc-agent audit-log
hpc-agent audit-log --result-status ok
hpc-agent audit-show <audit_id>
```

Each audit event records the actor, tool, input, decision, result status, diff summary,
redacted command argv, return codes, durations, config commits, and revert hints where
available.

## Common Commands

```bash
# Slurm read-only queries
hpc-agent node-status --node gpu01
hpc-agent queue --user alice
hpc-agent usage-report --account research --start 2026-05-01

# Slurm mutations, dry-run by default
hpc-agent assoc alice research --qos-add gpu
hpc-agent node-state gpu01 drain --reason maintenance
hpc-agent reservation maint-gpu create --nodes gpu01 --start 2026-06-01T01:00:00 --duration-min 60

# Ansible helpers
hpc-agent manage-inventory
hpc-agent compose-playbook site compute_gpu --roles common
hpc-agent lint-playbook /etc/hpc-agent/ansible/playbooks/site.yml

# Spack helpers
hpc-agent spack-envs
hpc-agent spack-spec "openmpi@5 +cuda"
hpc-agent spack-env my-env --op add_specs --specs "gcc@13" --specs "openmpi"
```

## Developer Checks

```bash
ruff check .
black --check .
mypy hpc_agent tests
pytest tests/unit
```

## Next Steps

- Read [USER_GUIDE.md](USER_GUIDE.md) for operator workflows and developer details.
- Read [agent-specs/README.md](agent-specs/README.md) for the design reference.
- Use the virtual-cluster notes in [deploy/README.md](deploy/README.md) when preparing
  integration testing.
