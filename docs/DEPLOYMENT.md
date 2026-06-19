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
hpc:
  slurm_bin_dir: /usr/bin      # directory containing scontrol, squeue, etc.
  warewulf_bin_dir: /usr/bin   # directory containing wwctl
  spack_root: /opt/spack
  ansible_dir: /etc/hpc-pilot/ansible
  config_repo: /etc/hpc-pilot/config
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
# Future AI agent integration (planned)
ANTHROPIC_API_KEY=sk-ant-...

# Platform bots (planned)
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

The AI agent / gateway layer is not yet implemented. Use CLI commands directly.

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
