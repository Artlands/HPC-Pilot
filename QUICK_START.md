# AutoHPC Quick Start

Get up and running with the HPC agent in 5 minutes.

## 1. Installation

```bash
git clone https://github.com/your-org/AutoHPC.git
cd AutoHPC
pip install -e ".[dev]"
```

## 2. Configuration

```bash
# Set minimal configuration
export HPC_CONFIG_REPO=/tmp/hpc-agent-config
export HPC_DRY_RUN_DEFAULT=true

# Create config directory
mkdir -p $HPC_CONFIG_REPO/policy
cp config_repo/policy/*.yaml $HPC_CONFIG_REPO/policy/
```

## 3. Initialize Database

```bash
# For testing, use SQLite
export HPC_DB_URL=sqlite+pysqlite:////tmp/hpc-agent-state.sqlite
export HPC_AUDIT_DB_URL=sqlite+pysqlite:////tmp/hpc-agent-audit.sqlite

# Initialize schema
alembic upgrade head
```

## 4. First Command (Dry-Run)

```bash
# List available tools
hpc-agent tools | head -20

# Check QOS (dry-run)
hpc-agent qos gpu

# Expected: shows current QOS config
```

## 5. Test a Mutating Operation

```bash
# Create/modify a QOS (still dry-run by default)
hpc-agent qos debug --op create --max-wall-min 60 --priority 100

# Shows what WOULD change
# Now actually apply:
hpc-agent qos debug --op create --max-wall-min 60 --priority 100 --apply
```

## 6. Common Operations

### Slurm

```bash
# View node status
hpc-agent node-status

# View queue
hpc-agent queue --user your-username

# Manage accounts
hpc-agent account mygroup --op modify --grp-tres cpu=128

# Drain a node
hpc-agent node-state gpu01 drain --reason "testing" --apply
```

### Spack

```bash
# List environments
hpc-agent spack-envs

# Create environment
hpc-agent spack-env my-env --op create

# Add specs
hpc-agent spack-env my-env --op add_specs --specs "gcc@13" "openmpi"
```

### Ansible

```bash
# Generate inventory
hpc-agent manage-inventory

# Compose playbook
hpc-agent compose-playbook my-playbook --target-group compute_gpu --roles common
```

## 7. Next Steps

1. Read `USER_GUIDE.md` for detailed documentation
2. Check `agent-specs/` for technical specifications
3. Run `pytest` to see tests in action
4. Configure approval backend in production

## CLI Cheat Sheet

| Command | Description |
|---------|-------------|
| `hpc-agent tools` | List all tools |
| `hpc-agent qos <name>` | Manage QOS |
| `hpc-agent account <name>` | Manage accounts |
| `hpc-agent node-status` | Show node status |
| `hpc-agent queue` | Show job queue |
| `hpc-agent spack-envs` | List Spack envs |
| `hpc-agent manage-inventory` | Generate Ansible inventory |
| `hpc-agent plan "<intent>"` | Build plan from intent |

## Troubleshooting

**"Command not found"**
```bash
pip install -e ".[dev]"  # Reinstall
```

**"Database not found"**
```bash
export HPC_DB_URL=sqlite+pysqlite:////tmp/test.db
alembic upgrade head
```

**"Config repo not found"**
```bash
export HPC_CONFIG_REPO=/tmp/hpc-config
mkdir -p $HPC_CONFIG_REPO/policy
```

## Examples

### Extend QOS wall time
```bash
hpc-agent qos gpu --max-wall-min 2880 --apply
```

### Add user to account
```bash
hpc-agent assoc alice research --qos-add gpu --apply
```

### Drain node for maintenance
```bash
hpc-agent node-state gpu01 drain --reason "memory upgrade" --apply
```

### Preview changes
All commands work in dry-run mode by default. Use `--apply` to execute.

### Batch operations
```bash
# Add 3 specs to environment
hpc-agent spack-env my-env --op add_specs \
  --specs "gcc@13" \
  --specs "openmpi" \
  --specs "cuda@12.4" \
  --apply
```
