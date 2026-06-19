# HPC Pilot — AI Agent for HPC Cluster Management

HPC Pilot is a Claude-powered command-line agent for HPC clusters.
It wraps Slurm, Warewulf, Ansible, and Spack with an AI chat interface,
Telegram/Discord bots, plus direct CLI commands with safety gates
(dry-run by default, RBAC, audit log).

---

## Installation

```bash
pip install hpc-pilot

# With Telegram and Discord gateway support
pip install 'hpc-pilot[gateway]'
```

### Prerequisites

| Component | Required for |
|-----------|-------------|
| Python 3.11+ | Always |
| `ANTHROPIC_API_KEY` | AI chat / gateway |
| Slurm (`scontrol`, `squeue`, `sacctmgr`) | Slurm commands |
| Warewulf 4.x (`wwctl`) | Warewulf commands |
| Spack (`spack`) | Spack commands |
| Ansible (`ansible-playbook`) | Ansible commands |

---

## Quick Start

```bash
# 1. Add your API key
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.hpc-pilot/.env

# 2. Start the AI chat
hpc-pilot chat

# 3. Ask anything
You: How many nodes are available?
You: Show me running jobs for user alice
You: Drain node gpu01 for maintenance (dry-run first, then confirm)
```

---

## Commands

### AI agent (requires ANTHROPIC_API_KEY)

```bash
hpc-pilot chat                    # interactive multi-turn chat
hpc-pilot chat -q "Show health"  # single query, non-interactive
hpc-pilot shell --role admin      # chat with explicit RBAC role
hpc-pilot gateway --start         # start Telegram + Discord bots
hpc-pilot gateway --status        # show which platforms are configured
hpc-pilot gateway --setup         # print setup instructions
```

### Direct cluster commands (no API key needed)

```bash
hpc-pilot health                            # cluster health check
hpc-pilot nodes [NODE]                      # Slurm node status
hpc-pilot queue [--user U] [--partition P]  # job queue
hpc-pilot qos NAME [--max-wall-min N]       # QOS (dry-run by default)
hpc-pilot qos NAME --apply [--yes]          # apply QOS change
hpc-pilot warewulf                          # Warewulf node list
hpc-pilot spack list|find ENV|compilers     # Spack queries
hpc-pilot ansible PLAYBOOK [--apply]        # Ansible playbook
hpc-pilot version                           # version info
```

---

## AI chat example

```
You: Show me the cluster health

Agent: [→ hpc_cluster_health_check]
I checked your cluster. Here's the summary:

| Component  | Status   | Notes                        |
|------------|----------|------------------------------|
| Slurm      | ✅ healthy | 48 nodes, 2 DOWN (gpu03,04) |
| Warewulf   | ✅ healthy | 48 nodes registered          |
| Spack      | ✅ healthy |                              |
| Ansible    | ✅ healthy |                              |

⚠️ Two nodes are DOWN: `gpu03`, `gpu04`. Run `hpc-pilot nodes gpu03` for details.

You: Drain gpu03 for hardware inspection

Agent: [→ hpc_slurm_node_status]  [→ hpc_slurm_node_state]
DRY-RUN: scontrol update node=gpu03 state=drain reason=hardware-inspection

This will prevent new jobs from scheduling on gpu03. Currently 3 jobs are
running; they will finish before the node is fully drained.

Shall I apply this change? (I'll set dry_run=false if you confirm.)

You: Yes, go ahead

Agent: [→ hpc_slurm_node_state]
Done. gpu03 is now DRAINING.
```

---

## Gateway (Telegram + Discord)

```bash
# ~/.hpc-pilot/.env
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456:ABC...    # from @BotFather
DISCORD_BOT_TOKEN=MTE...            # from Discord Developer Portal
HPC_PILOT_ROLE=admin

hpc-pilot gateway --start
```

Users can DM the Telegram bot or mention the Discord bot (`@HPC-Pilot`) to
get the same AI interface.  Each user gets an isolated conversation session.

---

## Safety model

Mutating commands are **dry-run by default** — the agent previews the command
before executing.

```
You: Change gpu QOS to 2-day wall time
Agent: DRY-RUN: sacctmgr --immediate modify qos gpu set MaxWall=2880
       Shall I apply this? ...
```

Approval flow:
| Mode | Behaviour |
|------|-----------|
| Default | prints DRY-RUN command, stops |
| `--apply` (CLI) | prompts `[y/N]` |
| `--apply --yes` | skips prompt (for scripts) |
| Agent confirms | sets `dry_run=false` only after user says yes |

---

## RBAC

```bash
export HPC_PILOT_ROLE=admin   # viewer | operator | admin
# or
echo '{"role": "operator"}' > ~/.hpc-pilot/.env
```

| Role | Allowed tools |
|------|---------------|
| `viewer` | node status, queue, health, Spack queries, Warewulf images |
| `operator` | viewer + node drain/resume |
| `admin` | operator + QOS modify, Ansible playbooks, Warewulf bootstrap |

---

## Audit log

Every tool invocation is appended to `~/.hpc-pilot/logs/audit.jsonl`:

```json
{"ts": 1750000000.0, "actor": "cli", "role": "admin", "tool": "hpc_slurm_qos_modify",
 "args": {"name": "gpu", "max_wall_min": 2880, "dry_run": false},
 "returncode": 0, "duration_ms": 43}
```

---

## Configuration

`~/.hpc-pilot/config.yaml` (auto-created on first run):

```yaml
model:
  default: claude-opus-4-7
  provider: anthropic

hpc:
  slurm_bin_dir: /usr/bin
  warewulf_bin_dir: /usr/bin
  spack_root: /opt/spack
  ansible_dir: /etc/hpc-pilot/ansible
  config_repo: /etc/hpc-pilot/config
```

`~/.hpc-pilot/.env`:
```bash
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...
HPC_PILOT_ROLE=admin
```

---

## Project structure

```
hpc_pilot/
├── cli.py       # CLI entry point + command handlers
├── gateway.py   # Telegram + Discord gateway
├── agent.py     # HpcAgent: Claude tool-use loop
├── tools.py     # HPC tool functions (Slurm, Warewulf, Spack, Ansible)
├── paths.py     # Home-directory path helpers
├── config.py    # Config initialization
├── rbac.py      # Role-Based Access Control
└── audit.py     # Audit logging

tests/
├── test_cli.py
├── test_gateway.py
├── test_tools.py
├── test_safety.py   # RBAC + audit tests
└── test_agent.py    # Agent loop + tool dispatch tests
```

---

## Development

```bash
pip install -e ".[dev,gateway]"
pytest tests/
```

---

## Planned

- `hpc-pilot tui` — text-based UI (Textual)
- `hpc-pilot cron` — scheduled monitoring jobs
- Session persistence across restarts
- Web UI (port 8000)
