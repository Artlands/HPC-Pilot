# HPC Pilot Documentation

This directory contains documentation for HPC Pilot - a standalone AI agent for HPC cluster management.

## Quick Start

### Installation

```bash
pip install hpc-pilot[anthropic]
hpc-pilot setup
```

That's it! HPC Pilot embeds Hermes Agent internally - no separate installation needed.

### Basic Usage

```bash
# Interactive chat
hpc-pilot

# Single query
hpc-pilot chat -q "Show cluster health"

# Gateway (web + platforms)
hpc-pilot gateway --start
```

## Directory Structure

```
docs/
├── README.md              # This file - user documentation
├── ARCHITECTURE.md        # Technical architecture
├── CONFIGURATION.md       # Configuration reference
├── TOOLS.md               # Tool reference
├── GATEWAY.md             # Gateway configuration
└── DEVELOPMENT.md         # Development guide
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                 HPC PILOT CLI                               │
│  - hpc-pilot (entrypoint)                                   │
│  - hpc-pilot gateway (web + platform)                       │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│           EMBEDDED HERMES AGENT                             │
│  - hermes-agent core (bundled)                              │
│  - Tool registry                                            │
│  - Gateway (Telegram, Discord, Slack)                       │
│  - Cron scheduler                                           │
└─────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              HPC PILOT TOOLS                                │
│  - Slurm, Warewulf, Ansible, Spack                          │
└─────────────────────────────────────────────────────────────┘
```

## Key Features

- ✅ Self-contained installation
- ✅ Gateway support (web + platforms)
- ✅ Persistent memory
- ✅ Cron jobs
- ✅ Skill learning

## Next Steps

1. Read [CONFIGURATION.md](CONFIGURATION.md) for setup
2. Read [TOOLS.md](TOOLS.md) for tool reference
3. Read [GATEWAY.md](GATEWAY.md) for gateway setup