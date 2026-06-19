# HPC Pilot — AI Agent for HPC Cluster Management

```
██╗  ██╗██████╗  █████╗      ██████╗ ██╗██╗      ██████╗ ████████╗
██║  ██║██╔══██╗██╔════╝     ██╔══██╗██║██║     ██╔═══██╗╚══██╔══╝
███████║██████╔╝██║          ██████╔╝██║██║     ██║   ██║   ██║
██╔══██║██╔═══╝ ██║          ██╔═══╝ ██║██║     ██║   ██║   ██║
██║  ██║██║     ╚██████╗     ██║     ██║███████╗╚██████╔╝   ██║
╚═╝  ╚═╝╚═╝      ╚═════╝     ╚═╝     ╚═╝╚══════╝ ╚═════╝    ╚═╝
```

HPC Pilot is an AI agent for HPC clusters built on **Hermes Agent**. It wraps
Slurm, Warewulf, Ansible, and Spack with an AI chat interface, Telegram/Discord
bots, plus direct CLI commands with safety gates (dry-run by default, RBAC,
audit log).

The agent engine is model-agnostic — use Anthropic Claude, OpenAI GPT, Google
Gemini, DeepSeek, or any provider Hermes Agent supports.

---

## Installation

HPC Pilot is not published to PyPI. Install from source:

```bash
# Clone and install
git clone https://github.com/Artlands/HPC-Pilot.git
cd hpc-pilot
pip install .

# With Telegram and Discord gateway support
pip install ".[gateway]"

# With development dependencies
pip install -e ".[dev,gateway]"
```

### Prerequisites

| Component | Required for |
|-----------|-------------|
| Python 3.11+ | Always |
| Slurm (`scontrol`, `squeue`, `sacctmgr`) | Slurm commands |
| Warewulf 4.x (`wwctl`) | Warewulf commands |
| Spack (`spack`) | Spack commands |
| Ansible (`ansible-playbook`) | Ansible commands |
| API key for your model provider | AI chat / gateway (default: Anthropic) |

---

## Quick Start

```bash
# 1. Create the config directory and default config.yaml
hpc-pilot setup

# 2. Install the Hermes Agent plugin
hpc-pilot setup-hermes

# 3. Set your API key (Anthropic, OpenAI, Gemini, etc.)
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.hpc-pilot/.env

# 4. Start the AI chat
hpc-pilot chat

# 5. Ask anything
You: How many nodes are available?
You: Show me running jobs for user alice
You: Drain node gpu01 for maintenance (dry-run first, then confirm)
```

> **First time?** Run `hpc-pilot setup` to create the config directory, then
> `hpc-pilot setup-hermes` to symlink the HPC-Pilot tool plugin into Hermes
> Agent so the AI model can call Slurm, Warewulf, Spack, and Ansible commands.

### Using a different provider

```bash
# OpenAI
echo 'OPENAI_API_KEY=sk-...' >> ~/.hpc-pilot/.env
hpc-pilot chat -m gpt-4o

# Gemini
echo 'GEMINI_API_KEY=...' >> ~/.hpc-pilot/.env
hpc-pilot chat -m gemini-2.0-flash

# DeepSeek / OpenRouter / any OpenAI-compatible provider
echo 'DEEPSEEK_API_KEY=...' >> ~/.hpc-pilot/.env
hpc-pilot chat -m deepseek-chat
```

Set the default model and provider in `~/.hpc-pilot/.env`:
```bash
HPC_PILOT_MODEL=claude-sonnet-4-6
```

---

## Commands

### AI agent

```bash
hpc-pilot chat                    # interactive multi-turn chat
hpc-pilot chat -q "Show health"  # single query, non-interactive
hpc-pilot chat -m gpt-4o         # specify model
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
hpc-pilot setup                            # create ~/.hpc-pilot/ and default config
hpc-pilot setup-hermes                     # install Hermes Agent plugin
hpc-pilot self-evolve [args]                # generate a new tool locally
hpc-pilot self-evolve-create-pr [args]      # push an evolved tool and open PR
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
| Slurm      | healthy  | 48 nodes, 2 DOWN (gpu03,04) |
| Warewulf   | healthy  | 48 nodes registered          |
| Spack      | healthy  |                              |
| Ansible    | healthy  |                              |

2 nodes are DOWN: gpu03, gpu04. Run `hpc-pilot nodes gpu03` for details.

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

## Hermes Agent Plugin

HPC-Pilot registers its cluster management tools as a Hermes Agent plugin.
This is what makes them available to the AI model during chat.

The plugin lives in the repo at `hpc_pilot/hermes_plugin/` and is installed
to `~/.hermes/plugins/hpc-pilot/` via:

```bash
hpc-pilot setup         # create ~/.hpc-pilot/ and default config
hpc-pilot setup-hermes  # symlink the plugin to ~/.hermes/plugins/hpc-pilot/
```

Both symlink and the Hermes plugin directory are created automatically.
Run `hpc-pilot setup-hermes` again after `git pull` to refresh.

### How it works

1. Hermes Agent loads the `hpc-pilot` plugin at startup
2. The plugin registers 114 tools (`hpc_slurm_*`, `hpc_warewulf_*`, etc.)
   as a Hermes toolset named `"hpc"`
3. Each tool call flows through: Hermes dispatch → RBAC check →
   audit logging → tool execution
4. Tool availability checks are mapped to subsystem probes
   (`check_slurm_available`, etc.)

---

## Self-Evolve

HPC-Pilot can **generate new tools on-demand** when it encounters an operation
no existing tool handles:

```
You: We need to check InfiniBand partition keys on compute nodes.

Agent: No existing tool handles this. I can evolve a new one.
       Shall I generate hpc_network_ib_list_partitions?

       hpc_self_evolve(tool_name="hpc_network_ib_list_partitions", ...)
       → generates tool code + tests + patches registration
       → runs pytest (all pass)
       → asks: "Create a PR to contribute this upstream?"

       hpc_self_evolve_create_pr(...)   # optional — push + PR
```

| Step | Tool | What it does |
|------|------|-------------|
| Generate | `hpc_self_evolve` | Creates tool `.py`, test `.py`, patches `__init__.py`, `agent.py`, `dispatch.py`, `rbac.py`, runs `pytest` |
| PR (optional) | `hpc_self_evolve_create_pr` | Commits, pushes to new branch, opens GitHub PR via API |

Generated tools land in `hpc_pilot/tools/evolved/`, keeping them cleanly
separated from hand-written tools. Requires `GITHUB_TOKEN` for PR creation.

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
export HPC_PILOT_ROLE=admin   # viewer | operator | admin | superadmin
# or
echo '{"role": "operator"}' > ~/.hpc-pilot/auth.json
```

| Role | Allowed tools |
|------|---------------|
| `viewer` | node status, queue, health, Spack queries, Warewulf images |
| `operator` | viewer + node drain/resume, job hold/cancel (own jobs only) |
| `admin` | operator + QOS modify, Ansible playbooks, Warewulf bootstrap |
| `superadmin` | admin + Slurm reconfig, accounting schema, infrastructure |

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

clusters:
  default:
    slurm_bin_dir: /usr/bin
    warewulf_bin_dir: /usr/bin
    spack_root: /opt/spack
    ansible_dir: /etc/hpc-pilot/ansible
```

`~/.hpc-pilot/.env`:
```bash
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=...
DISCORD_BOT_TOKEN=...
HPC_PILOT_ROLE=admin
HPC_PILOT_MODEL=claude-sonnet-4-6
```

---

## Project structure

```
hpc_pilot/
├── cli.py              # CLI entry point + command handlers
├── gateway.py          # Telegram + Discord gateway
├── agent.py            # HpcAgent: delegates to Hermes Agent subprocess
├── hermes_plugin/      # Hermes Agent plugin (tool schemas + registration)
│   ├── plugin.yaml
│   └── __init__.py
├── tools/              # HPC tool functions (Slurm, Warewulf, Spack, Ansible, ...)
├── dispatch.py         # Tool invocation: RBAC → audit → dispatch
├── rbac.py             # Role-Based Access Control
├── audit.py            # Audit logging (file, syslog, HTTP sinks)
├── approvals.py        # Out-of-band approval workflow
├── clusters.py         # Multi-cluster configuration
├── skills/             # Runbook/skill system
├── playbooks/          # Ansible drift-check playbooks
└── policy/             # Policy YAML files
```

---

## Development

```bash
pip install -e ".[dev,gateway]"
pytest tests/
```

Tests mock the Hermes subprocess and the underlying system binaries so no
real API keys or cluster access are needed.

---

## Architecture

```
┌──────────────┐     ┌──────────────────────────────┐
│  hpc-pilot   │     │      Hermes Agent             │
│  CLI/Gateway │────▶│  (model-agnostic runtime)     │
│  /Web UI     │     │                              │
└──────────────┘     │  ┌─────────────────────────┐  │
                     │  │  hpc-pilot plugin        │  │
                     │  │  93+ HPC tools           │  │
                     │  │  RBAC → Audit → Dispatch │  │
                     │  └─────────────────────────┘  │
                     │                              │
                     │  Claude · GPT · Gemini · ...  │
                     └──────────────────────────────┘
```
