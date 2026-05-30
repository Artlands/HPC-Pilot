# AutoHPC User Guide

AI agent that configures and manages HPC clusters (Warewulf, Ansible, Slurm, Spack).

## Quick Start

### Installation

```bash
git clone https://github.com/your-org/AutoHPC.git
cd AutoHPC
pip install -e ".[dev]"
```

### Configuration

Set up environment variables:

```bash
export HPC_DB_URL=postgresql+psycopg://hpcagent@localhost/hpc_agent
export HPC_AUDIT_DB_URL=postgresql+psycopg://hpcagent@localhost/hpc_audit
export HPC_CONFIG_REPO=/etc/hpc-agent/config
export HPC_SLURM_BIN_DIR=/usr/bin
export HPC_WW_BIN_DIR=/usr/bin
export HPC_SPACK_ROOT=/opt/spack
export HPC_ANSIBLE_DIR=/etc/hpc-agent/ansible
export HPC_APPROVAL_BACKEND=cli
export HPC_DRY_RUN_DEFAULT=true
```

Initialize the database:

```bash
alembic upgrade head
```

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        Interaction Layer                    │
│                      (CLI / HTTP / Chat)                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                      Agent Core                             │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │   Planner    │→ │  Executor    │→ │    Memory       │   │
│  │ (intent →    │  │ (run plan)   │  │ (cluster facts) │   │
│  │    steps)    │  │              │  │                 │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                     Safety Layer                            │
│  ┌──────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │     Diff     │  │  Policy      │  │   Approval      │   │
│  │   Preview    │  │  Engine      │  │   Gate          │   │
│  └──────────────┘  └──────────────┘  └─────────────────┘   │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                      Tools Layer                            │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐   │
│  │ Slurm  │ │Warewulf│ │Ansible │ │ Spack  │ │  State  │   │
│  │ Tools  │ │ Tools  │ │ Tools  │ │ Tools  │ │  Store  │   │
│  └────────┘ └────────┘ └────────┘ └────────┘ └─────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## CLI Reference

### General Usage

```bash
hpc-agent [OPTIONS] COMMAND [ARGS]...

Options:
  --help     Show this message and exit.

Commands:
  tools                    List registered tools and JSON schemas
  qos                      Manage Slurm QOS
  account                  Manage Slurm accounts
  assoc                    Manage Slurm user associations
  set-limits               Set limits on accounts/QOS/associations
  show-assoc               Show Slurm associations
  node-status              Show Slurm node status
  queue                    Show Slurm job queue
  job-accounting           Show completed job accounting
  usage-report             Show utilization reports
  diag                     Show Slurm controller diagnostics
  node-state               Drain/resume/down a node
  reservation              Manage Slurm reservations
  reconfigure              Reload Slurm configuration
  plan                     Build/execute plans from natural language
  lint-playbook            Lint Ansible playbooks
  compose-playbook         Compose Ansible playbooks from roles
  manage-inventory         Generate Ansible inventory
  run-playbook             Run Ansible playbooks
  check-secret             Verify secret references exist
  spack-envs               List Spack environments
  spack-find               List installed specs in Spack env
  spack-spec               Preview Spack concretization
  spack-compilers          Manage Spack compilers
  spack-env                Manage Spack environments
  spack-buildcache         Manage Spack buildcache
  spack-modules            Generate Spack modulefiles
  spack-view               Create Spack filesystem views
  spack-install            Install Spack packages
```

### Slurm Management

#### QOS Management

```bash
# View current QOS (dry-run)
hpc-agent qos gpu

# Extend wall time to 48 hours (dry-run)
hpc-agent qos gpu --op modify --max-wall-min 2880

# Actually apply the change
hpc-agent qos gpu --op modify --max-wall-min 2880 --apply

# Create a new QOS
hpc-agent qos batch --op create --max-wall-min 720 --priority 50
```

#### Account Management

```bash
# Modify existing account
hpc-agent account research --op modify --grp-tres cpu=512,gres/gpu=32

# Create new account in hierarchy
hpc-agent account pi_smith --op create --parent root --organization "Smith Lab"

# Set limits on account
hpc-agent set-limits account --name research --max-wall-min 4320
```

#### User Associations

```bash
# Add user to account with specific QOS
hpc-agent assoc alice research --qos-list gpu,normal --default-qos gpu

# Add QOS to existing association
hpc-agent assoc alice research --qos-add highpri

# Set fairshare value
hpc-agent assoc alice research --fairshare 100
```

#### Node Management

```bash
# View node status
hpc-agent node-status

# View specific node
hpc-agent node-status --node gpu01

# Drain a node for maintenance
hpc-agent node-state gpu01 drain --reason "maintenance" --apply

# Resume a drained node
hpc-agent node-state gpu01 resume --apply

# Put node down
hpc-agent node-state gpu01 down --reason "hardware failure" --apply
```

#### Job Queue & Accounting

```bash
# View queue for user
hpc-agent queue --user alice --partition gpu

# View queue for partition
hpc-agent queue --partition cpu

# Job accounting for user
hpc-agent job-accounting --user alice --start 2026-05-01 --end 2026-05-31

# Usage report
hpc-agent usage-report --account research --start 2026-05-01
```

#### Maintenance & Diagnostics

```bash
# Create maintenance reservation
hpc-agent reservation maint-gpu create \
  --nodes gpu01,gpu02 \
  --start 2026-06-01T01:00:00 \
  --duration-min 120

# Delete reservation
hpc-agent reservation maint-gpu delete

# Reconfigure Slurm controller
hpc-agent reconfigure --apply

# Controller diagnostics
hpc-agent diag
```

### Warewulf Provisioning

```bash
# Import container
hpc-agent import-container \
  --name rocky9 \
  --source docker://rockylinux:9 \
  --apply

# Build node image
hpc-agent build-node-image \
  --name gpu-rocky9 \
  --base-image rocky9 \
  --kind compute_gpu \
  --nvidia-driver-version 550.90.07 \
  --cuda-version 12.4 \
  --apply

# Define profile
hpc-agent define-profile \
  --name gpu-default \
  --image gpu-rocky9 \
  --apply

# Provision node
hpc-agent provision-node \
  --hostname gpu01 \
  --mac 00:11:22:33:44:55 \
  --ip 10.0.0.101 \
  --profile gpu-default \
  --role compute_gpu \
  --apply

# Assign image to nodes
hpc-agent assign-image-to-nodes \
  --nodes gpu01,gpu02 \
  --profile gpu-default \
  --apply

# Rebuild overlay
hpc-agent rebuild-overlay --node gpu01 --apply
```

### Ansible Configuration Management

```bash
# List all registered tools
hpc-agent tools

# Lint a playbook
hpc-agent lint-playbook /etc/hpc-agent/ansible/playbooks/site.yml

# Compose playbook from roles
hpc-agent compose-playbook \
  site \
  --target-group compute_gpu \
  --roles common,chrony,munge,slurm_client \
  --apply

# Generate inventory from state store
hpc-agent manage-inventory --apply

# Run playbook on specific nodes
hpc-agent run-playbook site --limit gpu01,gpu02

# Check secret reference exists
hpc-agent check-secret munge/key
```

### Spack Software Management

```bash
# List environments
hpc-agent spack-envs

# Find specs in environment
hpc-agent spack-find --env gpu-stack

# Preview concretization
hpc-agent spack-spec "openmpi@5 +cuda"

# Find/modify compilers
hpc-agent spack-compilers --op find --scope site
hpc-agent spack-compilers --op add --scope site --path /opt/gcc/bin --apply

# Create environment
hpc-agent spack-env my-env --op create

# Add specs to environment
hpc-agent spack-env my-env --op add_specs \
  --specs "gcc@13" "openmpi" "cuda@12.4"

# Generate modulefiles
hpc-agent spack-modules gpu-stack --module-type lmod

# Create filesystem view
hpc-agent spack-view gpu-stack --prefix /opt/modules

# Install packages (dry-run)
hpc-agent spack-install gpu-stack

# Actually install
hpc-agent spack-install gpu-stack --apply
```

## Workflow Examples

### Adding a New GPU Node

```bash
# 1. Provision the node
hpc-agent provision-node \
  --hostname gpu03 \
  --mac 00:11:22:33:44:56 \
  --ip 10.0.0.103 \
  --profile gpu-default \
  --role compute_gpu

# 2. Add to partition
hpc-agent compute-gpu-add-node --node gpu03 --partition gpu

# 3. Verify
hpc-agent node-status --node gpu03
```

### Extending User Resources

```bash
# Give user 48 hours wall time on GPU QOS
hpc-agent plan "give alice 48 hours of wall time on the gpu qos" --apply
```

### Rolling Node Update

```bash
# Roll out new image to all GPU nodes
python3 -m hpc_agent.workflows.rolling_update \
  --group compute_gpu \
  --new-image gpu-rocky9-v2 \
  --batch-size 2 \
  --actor admin
```

### State Reconciliation

```bash
# Check for drift between desired and live state
python3 -m hpc_agent.workflows.reconcile --actor admin
```

## Safety & Approval System

### Dry-Run Mode (Default)

All mutating tools run in dry-run mode by default, showing what would change:

```bash
$ hpc-agent qos gpu --max-wall-min 2880
{
  "status": "dry_run",
  "diff": {
    "changes": [
      {
        "target": "qos/gpu",
        "field": "max_wall_min",
        "before": "1440",
        "after": "2880",
        "op": "modify"
      }
    ]
  }
}
```

### Approval Gates

Tools are categorized by risk level:
- **READ**: Auto-run (no approval needed)
- **LOW**: Reversible, auto-run within policy bounds
- **MEDIUM**: Requires approval unless in-policy
- **HIGH**: Always requires approval

### Policy Engine

Policy rules are defined in YAML in `config_repo/policy/`:

```yaml
# policy/slurm.yaml
- id: qos-wall-cap
  match: { tool: "slurm.manage_qos" }
  assert:
    max_wall_min: { "<=": 4320 }
  on_violation: deny
  message: "QOS wall time above 3d requires manual approval"

- id: qos-extend-autoallow
  match: { tool: "slurm.manage_qos", op: "modify" }
  assert:
    max_wall_min: { "<=": 2880 }
    max_tres.gpu: { "<=": 16 }
  effect: auto
```

### Rollback

Every change retrieves state before the action. To rollback:

```bash
# View audit history
hpc-agent audit list

# Rollback specific audit ID
hpc-agent rollback <audit_id>
```

## Configuration

### Settings

Configuration is via environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `HPC_DB_URL` | PostgreSQL connection string | `postgresql+psycopg://hpcagent@localhost/hpc_agent` |
| `HPC_AUDIT_DB_URL` | Audit database URL | Same as DB_URL |
| `HPC_CONFIG_REPO` | Git repository for config files | `/etc/hpc-agent/config` |
| `HPC_SLURM_BIN_DIR` | Slurm binary directory | `/usr/bin` |
| `HPC_WW_BIN_DIR` | Warewulf binary directory | `/usr/bin` |
| `HPC_SPACK_ROOT` | Spack installation path | `/opt/spack` |
| `HPC_ANSIBLE_DIR` | Ansible directory | `/etc/hpc-agent/ansible` |
| `HPC_APPROVAL_BACKEND` | Approval method: cli, slack, api | `cli` |
| `HPC_DRY_RUN_DEFAULT` | Default to dry-run mode | `true` |
| `HPC_MAX_BLAST_RADIUS_AUTO` | Max nodes for auto-run | `4` |

### RBAC Roles

| Role | Capabilities |
|------|-------------|
| **viewer** | All read actions: `*.query*`, `*.list*`, `*.status*` |
| **operator** | Slurm, Warewulf, Ansible (limited), Spack |
| **admin** | Full access: `*` |

## Development

### Adding a New Tool

1. Create tool function with Pydantic input model:

```python
from hpc_agent.tools.base import tool, Risk
from pydantic import BaseModel

class MyToolIn(BaseModel):
    name: str
    value: int

@tool(name="mytool.dosomething", risk=Risk.LOW, domain="mytool")
def do_something(inp: MyToolIn, *, actor: str) -> ToolResult:
    """Brief description."""
    # Implementation follows spec 00 §3.4 execution contract
    pass
```

2. Test with dry-run

3. Add to `hpc_agent/tools/__init__.py` exports

4. Add CLI command in `hpc_agent/core/interaction.py`

### Testing

```bash
# Run all tests
pytest

# Run specific test
pytest tests/unit/test_manage_qos.py -v

# Type checking
mypy hpc_agent/

# Linting
ruff check hpc_agent tests
```

## Troubleshooting

### Common Issues

**Database connection failed**
```bash
# Verify PostgreSQL is running
pg_isready

# Test connection
psql $HPC_DB_URL -c "SELECT 1"
```

**Command not allowed**
```bash
# Check allowlist in hpc_agent/exec/runner.py
# Add binary to ALLOWLIST if needed
```

**Approval stuck**
```bash
# Check approval backend configuration
# For CLI: y/N prompt required
# For API: check approval status endpoint
```

### Logging

All actions are logged to audit table. Query audit log:

```bash
# View last 10 actions
hpc-agent audit list | tail -10
```

## API Reference (Programmatic)

```python
from hpc_agent.tools.slurm import manage_qos, extend_account
from hpc_agent.tools.base import tool_schemas
from hpc_agent.safety.policy import PolicyEngine
from hpc_agent.core.planner import build_plan
from hpc_agent.core.executor import run_plan

# Get tool schemas for LLM
tools = tool_schemas()

# Build plan from intent
plan = build_plan("extend wall time for gpu qos", actor="admin")

# Execute plan
result = run_plan(plan, actor_role=Role.OPERATOR)

# Use tool directly
result = manage_qos(
    ManageQOSIn(name="gpu", op="modify", max_wall_min=2880),
    actor="admin",
    actor_role=Role.OPERATOR
)
```

## Best Practices

1. **Always test in dry-run first**
2. **Use rolling updates for node changes**
3. **Check reconciled state before major changes**
4. **Review audit logs regularly**
5. **Keep policy files version-controlled**
6. **Use structured node naming conventions**
7. **Test tool changes in non-production first**

## Support

- Documentation: https://github.com/your-org/AutoHPC/docs
- Issues: https://github.com/your-org/AutoHPC/issues
- Specs: `agent-specs/00-foundations.md` through `08-testing.md`
