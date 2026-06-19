# HPC Pilot - AI Agent for HPC Cluster Management

HPC Pilot is a standalone AI agent for HPC cluster management, built on Hermes Agent.

## Features

- **Self-contained installation** - `pip install hpc-pilot` is all you need
- **Gateway support** - Telegram, Discord, Slack built-in
- **Persistent memory** - Learn across sessions
- **Cron jobs** - Scheduled cluster monitoring
- **Skill learning** - Document workflows for reuse

## Installation

### Prerequisites
- Python 3.11 or higher
- Slurm (for Slurm management)
- Warewulf (optional, for Warewulf management)
- Spack (optional, for Spack management)
- Ansible (optional, for Ansible management)

### Quick Start

```bash
# Install HPC Pilot (includes Hermes Agent)
pip install hpc-pilot[anthropic]

# Or with OpenAI support
pip install hpc-pilot[openai]

# Initial setup (creates ~/.hpc-pilot/)
hpc-pilot setup
```

That's it! No need to install Hermes Agent separately.

## Usage

### CLI Interface

```bash
# Start interactive chat
hpc-pilot
hpc-pilot chat

# Single query
hpc-pilot chat -q "Show cluster health"

# Start shell
hpc-pilot shell

# Start text-based UI
hpc-pilot tui

# Check cluster health
hpc-pilot health

# Show node status
hpc-pilot nodes
hpc-pilot nodes gpu01

# Show job queue
hpc-pilot queue
hpc-pilot queue --user alice

# Manage QOS
hpc-pilot qos gpu --max-wall-min 2880 --apply

# Show Warewulf status
hpc-pilot werewulf

# Spack commands
hpc-pilot spack list
hpc-pilot spack find my-env
hpc-pilot spack compilers

# Ansible commands
hpc-pilot ansible /path/to/playbook.yml --apply

# View version
hpc-pilot version
```

### Gateway (Web + Platforms)

```bash
# Start gateway server
hpc-pilot gateway --start

# Configure gateway
hpc-pilot gateway --setup

# Gateway serves web UI on http://localhost:8000
# Also supports Telegram, Discord, Slack
```

#### Gateway Configuration

1. Create `~/.hpc-pilot/.env`:
```bash
ANTHROPIC_API_KEY=***
TELEGRAM_BOT_TOKEN=***
DISCORD_BOT_TOKEN=***
```

2. Configure platforms in `~/.hpc-pilot/config.yaml`:
```yaml
gateway:
  enabled: true
  platforms:
    telegram:
      enabled: true
    discord:
      enabled: true
```

3. Start gateway:
```bash
hpc-pilot gateway --start
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     HPC PILOT CLI                           │
│  - hpc-pilot (entrypoint)                                   │
│  - hpc-pilot gateway (web + platform support)               │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              EMBEDDED HERMES AGENT                          │
│  - hermes-agent core (bundled)                              │
│  - Tool registry (auto-discovery)                           │
│  - Gateway (Telegram, Discord, Slack)                      │
│  - Cron scheduler                                           │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                HPC PILOT TOOLS                              │
│  - Slurm: QOS, nodes, queues                                │
│  - Warewulf: nodes, images                                  │
│  - Ansible: playbooks, inventory                            │
│  - Spack: environments, compilers                           │
└─────────────────────────────────────────────────────────────┘
```

## Configuration

Configuration is stored in `~/.hpc-pilot/`:

```
~/.hpc-pilot/
├── config.yaml      # Main configuration
├── .env             # Environment variables
├── skills/          # User skills
├── sessions/        # Session history
├── logs/            # Log files
└── state.db         # Database
```

### Configuration File

```yaml
# ~/.hpc-pilot/config.yaml
model:
  default: anthropic/claude-sonnet-4
  provider: anthropic

agent:
  max_turns: 90
  approvals:
    mode: smart

hpc:
  slurm_bin_dir: /usr/bin
  warewulf_bin_dir: /usr/bin
  spack_root: /opt/spack
  ansible_dir: /etc/hpc-pilot/ansible
  config_repo: /etc/hpc-pilot/config

gateway:
  enabled: true
  port: 8000
  platforms:
    telegram:
      enabled: true
    discord:
      enabled: true
```

### Environment Variables

```bash
# ~/.hpc-pilot/.env
ANTHROPIC_API_KEY=your-api-key
TELEGRAM_BOT_TOKEN=your-telegram-token
DISCORD_BOT_TOKEN=your-discord-token
SLACK_BOT_TOKEN=***

# HPC Cluster Environment
HPC_SLURM_BIN_DIR=/usr/bin
HPC_WAREWOLF_BIN_DIR=/usr/bin
HPC_SPACK_ROOT=/opt/spack
HPC_CONFIG_REPO=/etc/hpc-pilot/config
```

## Tool Coverage

| Tool Domain | Hermes Tool | CLI Command |
|------------|-------------|-------------|
| Slurm | `hpc_slurm_node_status` | `hpc-pilot nodes` |
| Slurm | `hpc_slurm_queue` | `hpc-pilot queue` |
| Slurm | `hpc_slurm_node_state` | `hpc-pilot node-state` |
| Slurm | `hpc_slurm_qos_modify` | `hpc-pilot qos` |
| Warewulf | `hpc_warewulf_node_status` | `hpc-pilot werewulf` |
| Warewulf | `hpc_warewulf_image_list` | `hpc-pilot werewulf images` |
| Warewulf | `hpc_warewulf_bootstrap` | `hpc-pilot werewulf bootstrap` |
| Spack | `hpc_spack_env_list` | `hpc-pilot spack list` |
| Spack | `hpc_spack_find` | `hpc-pilot spack find` |
| Spack | `hpc_spack_compilers` | `hpc-pilot spack compilers` |
| Ansible | `hpc_ansible_playbook_run` | `hpc-pilot ansible` |
| Ansible | `hpc_ansible_inventory_generate` | `hpc-pilot ansible inventory` |
| Cluster | `hpc_cluster_health_check` | `hpc-pilot health` |

## Gateway Features

### Web UI
Visit `http://localhost:8000` after starting the gateway.

### Telegram Bot
1. Create bot via @BotFather
2. Add token to `~/.hpc-pilot/.env`
3. Enable in config.yaml
4. Start gateway

### Discord Bot
1. Create application at Discord Developer Portal
2. Add token to `~/.hpc-pilot/.env`
3. Enable in config.yaml
4. Start gateway

### Slack App
1. Create app at Slack Developer Portal
2. Add token to `~/.hpc-pilot/.env`
3. Enable in config.yaml
4. Start gateway

## Cron Jobs

```bash
# List cron jobs
hpc-pilot cron list

# Create cron job (runs every 30 minutes)
hpc-pilot cron create "30m" -p "Run cluster health check"

# Create weekly report (every Monday at 9am)
hpc-pilot cron create "0 9 * * 1" -p "Generate weekly cluster usage report"
```

## Skill Learning

Skills are stored in `~/.hpc-pilot/skills/`:

```bash
# List skills
hpc-pilot skills list

# Install skill
hpc-pilot skills install hpc:qos-management

# Create new skill
hpc-pilot skills create my-workflow
```

## Development

### Run Tests

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/
```

### Build Documentation

```bash
# Build docs
python -m build
```

### Project Structure

```
hpc_pilot/
├── __init__.py           # Package init
├── cli.py                # CLI entrypoint
├── gateway.py            # Gateway server
├── _hermes.py            # Hermes integration
├── tools.py              # HPC tools
├── plugins/              # Plugin modules
│   ├── slurm.py
│   ├── warewulf.py
│   ├── ansible.py
│   └── spack.py
└── __pycache__/

tests/                    # Test suite
docs/                     # Documentation
```

## Troubleshooting

### Hermes not found
```bash
# Reinstall with Hermes support
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
# Check if tools are registered
hpc-pilot tools

# Rebuild tool registry
hpc-pilot tools rebuild
```

## License

MIT License - See LICENSE file for details.

## Support

- Documentation: https://hpc-pilot.readthedocs.io/
- GitHub: https://github.com/your-org/hpc-pilot
- Issues: https://github.com/your-org/hpc-pilot/issues