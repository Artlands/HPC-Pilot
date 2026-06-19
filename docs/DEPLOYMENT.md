# HPC Pilot Deployment Guide

## Quick Start

```bash
pip install hpc-pilot
hpc-pilot health
```

## Prerequisites

- Python 3.11+
- Slurm toolchain (`scontrol`, `squeue`, `sacctmgr`) for Slurm commands
- Warewulf 4.x (`wwctl`) for Warewulf commands — optional
- Spack (`spack`) for Spack commands — optional
- Ansible (`ansible-playbook`, `ansible-inventory`) — optional

## Installation Options

```bash
# Standard
pip install hpc-pilot

# Development (with linting/testing tools)
pip install -e ".[dev]"
```

## Configuration

The first command run creates `~/.hpc-pilot/config.yaml` with defaults.

```yaml
model:
  default: claude-opus-4-7

clusters:
  default:
    slurm_bin_dir: /usr/bin      # directory containing scontrol, squeue, etc.
    warewulf_bin_dir: /usr/bin   # directory containing wwctl
    spack_root: /opt/spack
    ansible_dir: /etc/hpc-pilot/ansible
    # Optional SSH config when the Slurm controller is remote:
    # ssh:
    #   host: head01.example.com
    #   user: hpcadmin
    #   key: ~/.ssh/hpc-pilot

default_cluster: default
```

## Role setup

```bash
# Via environment variable (all commands in the session)
export HPC_PILOT_ROLE=admin

# Via file (persists)
echo '{"role": "operator"}' > ~/.hpc-pilot/auth.json
```

Roles: `viewer` (read-only) → `operator` (+ node state) → `admin` (+ QOS, Ansible, bootstrap)

## Secrets / API keys

Create `~/.hpc-pilot/.env` (not auto-created):

```bash
# AI agent (required for hpc-pilot chat and gateway)
ANTHROPIC_API_KEY=sk-ant-...

# Platform bots (for Telegram/Discord gateway)
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...
```

## Production: systemd service

HPC Pilot currently runs as a one-shot CLI, not a persistent daemon.
To run scheduled health checks, use cron:

```bash
# /etc/cron.d/hpc-pilot-health
*/30 * * * * hpcadmin HPC_PILOT_ROLE=viewer hpc-pilot health >> /var/log/hpc-pilot-health.log 2>&1
```

## Monitoring

```bash
# Live audit log
tail -f ~/.hpc-pilot/logs/audit.jsonl | python3 -m json.tool

# Cluster health check
hpc-pilot health
```

## Troubleshooting

**`No such file or directory: 'scontrol'`**

Slurm binaries are not on PATH. Either add them to PATH or set `HPC_SLURM_BIN_DIR`:

```bash
export PATH="/usr/local/slurm/bin:$PATH"
```

**`Permission denied: Tool '...' requires role 'admin'`**

Set a higher role: `export HPC_PILOT_ROLE=admin`

**`Gateway won't start`**

Ensure `HPC_PILOT_TELEGRAM_TOKEN` (Telegram) or `HPC_PILOT_DISCORD_TOKEN` (Discord) is set in `~/.hpc-pilot/.env` or the environment. Run `hpc-pilot gateway --setup` to configure tokens interactively. If neither platform token is provided the gateway exits immediately with no error message.

## Backup and restore

```bash
# Backup config and audit log
tar -czf hpc-pilot-backup.tar.gz ~/.hpc-pilot/

# Restore
tar -xzf hpc-pilot-backup.tar.gz -C ~/
```

## Update

```bash
pip install --upgrade hpc-pilot
```
