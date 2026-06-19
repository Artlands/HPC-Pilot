# HPC Pilot Architecture

This document describes the technical architecture of HPC Pilot.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        USER INTERFACE                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │   CLI        │  │  Gateway     │  │  Platform Apps             │   │
│  │  (typer)     │  │  (web)       │  │  (Telegram, Discord, etc.)│   │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   EMBEDDED HERMES AGENT                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐   │
│  │  CLI Layer   │  │ Gateway Layer│  │ Agent Core               │   │
│  │  (hpc_pilot) │  │  (hpc_pilot) │  │  (hermes-agent)          │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│                   HPC PILOT TOOLS                                     │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────────────────────┐ │
│  │ Slurm    │ │Warewulf  │ │ Ansible  │ │  Spack                │ │
│  │ tools.py │ │tools.py  │ │tools.py  │ │  tools.py             │ │
│  └──────────┘ └──────────┘ └──────────┘ └─────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

## Components

### 1. CLI Layer (`hpc_pilot/cli.py`)

**Purpose**: Entry point for command-line usage

```python
def main() -> int:
    """Main entry point."""
    # Initialize home and config
    init_home()
    init_config()
    
    # Parse CLI arguments
    # Dispatch to appropriate command
```

**Key Features**:
- Argument parsing with `argparse`
- Command routing (chat, shell, tui, gateway, etc.)
- Home directory initialization
- Config file generation

### 2. Gateway Layer (`hpc_pilot/gateway.py`)

**Purpose**: Web and platform gateway server

```python
def main() -> int:
    """Gateway server entry point."""
    # Initialize home and config
    init_home()
    init_config()
    
    # Start web server
    # Configure platform connections
```

**Key Features**:
- HTTP server on port 8000
- Telegram bot support
- Discord bot support
- Slack bot support

### 3. Hermes Integration (`hpc_pilot/_hermes.py`)

**Purpose**: Embed and extend Hermes Agent

```python
def run_cli(args: list[str]) -> int:
    """Run Hermes CLI with HPC config."""
    # Set Hermes environment variables
    os.environ["HERMES_HOME"] = get_home()
    os.environ["HERMES_CONFIG"] = get_config_path()
    
    # Import and run Hermes CLI
    from hermes_cli.main import main as hermes_main
    return hermes_main(args)
```

**Key Features**:
- Environment variable setup
- Config path override
- Toolset registration

### 4. Tool Modules (`hpc_pilot/tools.py`)

**Purpose**: HPC-specific tools for Hermes Agent

```python
def hpc_slurm_node_status(node: str) -> str:
    """Get Slurm node status."""
    result = subprocess.run(["scontrol", "show", "node", node])
    return result.stdout

# Register with Hermes registry
registry.register(
    name="hpc_slurm_node_status",
    toolset="hpc",
    schema={...},
    handler=hpc_slurm_node_status,
    check_fn=check_slurm_available,
)
```

**Key Features**:
- 12+ HPC tools
- Automatic tool registration
- Requirement checking

## Data Flow

### CLI Command Flow

```
User Command (hpc-pilot health)
         │
         ▼
CLI main() → init_home() → init_config()
         │
         ▼
Parse arguments → health_command()
         │
         ▼
hpc_pilot.tools.hpc_cluster_health_check()
         │
         ▼
Return JSON to user
```

### Gateway Request Flow

```
HTTP Request (POST /api/chat)
         │
         ▼
Gateway main() → init_home() → init_config()
         │
         ▼
Route to Hermes Agent
         │
         ▼
Run conversation loop
         │
         ▼
Return response
```

### Tool Call Flow

```
User Query ("Show cluster health")
         │
         ▼
Hermes CLI → parse query
         │
         ▼
Hermes tool registry → find hpc tools
         │
         ▼
Run hpc_cluster_health_check()
         │
         ▼
Return structured response
```

## Configuration

### Home Directory Structure

```
~/.hpc-pilot/
├── config.yaml          # Main configuration
├── .env                 # Environment variables
├── skills/              # User-defined skills
├── sessions/            # Session history
├── logs/                # Log files
└── state.db             # SQLite database
```

### Configuration Process

1. **First Run**:
   - Detect `~/.hpc-pilot/` doesn't exist
   - Create directory structure
   - Generate default `config.yaml`
   - Generate default `.env` (empty, user fills in)

2. **Subsequent Runs**:
   - Load existing `config.yaml`
   - Apply any environment variables
   - Run command

### Configuration Options

```yaml
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

toolsets:
  hpc:
    enabled: true
```

## Tool Registration

HPC Pilot registers tools with Hermes Agent registry:

```python
from tools.registry import registry

registry.register(
    name="hpc_slurm_node_status",
    toolset="hpc",
    schema={
        "name": "hpc_slurm_node_status",
        "description": "Get Slurm node status",
        "parameters": {
            "type": "object",
            "properties": {"node": {"type": "string"}},
            "required": ["node"]
        }
    },
    handler=lambda args, **kw: hpc_slurm_node_status(args.get("node", "")),
    check_fn=check_slurm_available,
)
```

## Error Handling

### Missing Dependencies

```python
try:
    from hermes_cli.main import main as hermes_main
    return hermes_main(args)
except ImportError as e:
    print(f"Error: {e}")
    print("Install with: pip install hpc-pilot[anthropic]")
    return 1
```

### Configuration Errors

```python
def init_config() -> str:
    home = init_home()
    config_path = os.path.join(home, "config.yaml")
    
    if not os.path.exists(config_path):
        # Generate default config
        with open(config_path, "w") as f:
            f.write(DEFAULT_CONFIG)
    
    return config_path
```

## Performance

### CLI Latency
- Cold start: ~2-5 seconds
- Hot start: ~1-2 seconds
- Tool execution: depends on command

### Gateway Throughput
- Static files: ~1000 req/s
- API endpoints: ~100 req/s
- Real-time: WebSockets for streaming

## Security

### Secret Redaction
- Hermes Agent's built-in secret redaction
- Config files excluded from logs
- Environment variables masked

### File Access
- Tools run in sandboxed environment
- File access limited to HPC paths
- Audit logging for all operations

## Deployment

### Production Setup

1. **Install**:
   ```bash
   pip install hpc-pilot[anthropic]
   ```

2. **Configure**:
   ```bash
   hpc-pilot setup
   ```

3. **Set credentials**:
   ```bash
   # Edit ~/.hpc-pilot/.env
   ANTHROPIC_API_KEY=***
   ```

4. **Start gateway**:
   ```bash
   hpc-pilot gateway --start
   ```

5. **Deploy**:
   - Run as systemd service
   - Configure HTTPS
   - Set up reverse proxy (nginx)