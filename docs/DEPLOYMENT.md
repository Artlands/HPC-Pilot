# HPC Pilot Deployment Guide

This guide shows how to deploy HPC Pilot as a standalone application.

## Quick Start

```bash
# Install
pip install hpc-pilot[anthropic]

# Setup (creates ~/.hpc-pilot/)
hpc-pilot setup

# Start using
hpc-pilot
```

That's it! HPC Pilot embeds Hermes Agent internally.

## Prerequisites

- Python 3.11 or higher
- Slurm (for Slurm management)
- Optional: Warewulf, Spack, Ansible

## Installation Options

### Standard Installation

```bash
pip install hpc-pilot[anthropic]
```

### Development Installation

```bash
pip install -e ".[dev,anthropic]"
```

### With All Features

```bash
pip install hpc-pilot[dev,anthropic,openai]
```

## Configuration

### First Run

```bash
hpc-pilot setup
```

This creates `~/.hpc-pilot/` with default configuration.

### Configuration File

Location: `~/.hpc-pilot/config.yaml`

```yaml
model:
  default: anthropic/claude-sonnet-4
  provider: anthropic

hpc:
  slurm_bin_dir: /usr/bin
  warewulf_bin_dir: /usr/bin
  spack_root: /opt/spack
  ansible_dir: /etc/hpc-pilot/ansible
  config_repo: /etc/hpc-pilot/config
```

### Environment Variables

Location: `~/.hpc-pilot/.env`

```bash
ANTHROPIC_API_KEY=***
```

## Usage

### CLI

```bash
# Start interactive chat
hpc-pilot

# Single query
hpc-pilot chat -q "Show cluster health"

# Shell
hpc-pilot shell

# TUI
hpc-pilot tui

# Check health
hpc-pilot health

# Node status
hpc-pilot nodes
hpc-pilot nodes gpu01

# Job queue
hpc-pilot queue
hpc-pilot queue --user alice --partition gpu

# QOS management
hpc-pilot qos gpu --max-wall-min 2880 --apply

# Warewulf
hpc-pilot werewulf

# Spack
hpc-pilot spack list
hpc-pilot spack find my-env

# Ansible
hpc-pilot ansible playbook.yml --apply

# Version
hpc-pilot version
```

### Gateway

```bash
# Start gateway
hpc-pilot gateway --start

# Visit web UI
open http://localhost:8000

# Configure platforms
hpc-pilot gateway --setup
```

### Cron Jobs

```bash
# List cron jobs
hpc-pilot cron list

# Create health check cron
hpc-pilot cron create "30m" -p "Run cluster health check"

# Create weekly report
hpc-pilot cron create "0 9 * * 1" -p "Generate weekly cluster usage report"
```

## Platform Integration

### Telegram

1. Create bot via @BotFather
2. Add token to `~/.hpc-pilot/.env`:
   ```bash
   TELEGRAM_BOT_TOKEN=***
   ```
3. Configure in `config.yaml`:
   ```yaml
   gateway:
     platforms:
       telegram:
         enabled: true
   ```
4. Start gateway:
   ```bash
   hpc-pilot gateway --start
   ```

### Discord

1. Create application at Discord Developer Portal
2. Add token to `~/.hpc-pilot/.env`:
   ```bash
   DISCORD_BOT_TOKEN=***
   ```
3. Configure in `config.yaml`
4. Start gateway

### Slack

1. Create app at Slack Developer Portal
2. Add token to `~/.hpc-pilot/.env`:
   ```bash
   SLACK_BOT_TOKEN=***
   ```
3. Configure in `config.yaml`
4. Start gateway

## Production Deployment

### Systemd Service

```bash
# Create service file
sudo cat > /etc/systemd/system/hpc-pilot-gateway.service << EOF
[Unit]
Description=HPC Pilot Gateway
After=network.target

[Service]
Type=simple
User=hpc-pilot
WorkingDirectory=/opt/hpc-pilot
ExecStart=/usr/local/bin/hpc-pilot gateway --start
Restart=always
RestartSec=10
Environment=HPC_PILOT_HOME=/home/hpc-pilot/.hpc-pilot

[Install]
WantedBy=multi-user.target
EOF

# Reload and start
sudo systemctl daemon-reload
sudo systemctl enable hpc-pilot-gateway
sudo systemctl start hpc-pilot-gateway
```

### HTTPS with Nginx

```nginx
server {
    listen 443 ssl;
    server_name hpc.example.com;

    ssl_certificate /etc/ssl/certs/hpc.crt;
    ssl_certificate_key /etc/ssl/private/hpc.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Docker Deployment

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    slurm-wlm \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir hpc-pilot[anthropic]

COPY . .

CMD ["hpc-pilot", "gateway", "--start"]
```

## Monitoring

### Logs

```bash
# View logs
cat ~/.hpc-pilot/logs/*.log
```

### Health Checks

```bash
# Check cluster health
hpc-pilot health

# Check gateway status
hpc-pilot gateway --status
```

## Maintenance

### Backup

```bash
# Backup configuration
tar -czf hpc-pilot-backup.tar.gz ~/.hpc-pilot/
```

### Update

```bash
# Update HPC Pilot
pip install --upgrade hpc-pilot[anthropic]

# Reconfigure if needed
hpc-pilot setup
```

### Restore

```bash
# Restore from backup
tar -xzf hpc-pilot-backup.tar.gz -C ~/
```

## Troubleshooting

### Hermes not found

```bash
# Reinstall
pip install hpc-pilot[anthropic] --force-reinstall
```

### Gateway won't start

```bash
# Check logs
ls ~/.hpc-pilot/logs/

# Reconfigure
hpc-pilot gateway --setup
```

### Tools not available

```bash
# Check tool registration
hpc-pilot tools

# Rebuild registry
hpc-pilot tools rebuild
```

### Permission issues

```bash
# Fix permissions
chmod -R 755 ~/.hpc-pilot/
```