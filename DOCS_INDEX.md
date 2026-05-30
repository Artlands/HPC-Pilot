# AutoHPC Documentation Index

## Overview

AutoHPC is an AI agent that configures and manages HPC clusters using:
- **Warewulf**: Node provisioning and image management
- **Ansible**: Configuration management
- **Slurm**: Job scheduler and resource management
- **Spack**: Software stack management

## Documentation

### Getting Started
- **[QUICK_START.md](./QUICK_START.md)** - Get running in 5 minutes
  - Installation
  - Configuration
  - First commands
  - Common operations
  - CLI cheat sheet

### Full Documentation
- **[USER_GUIDE.md](./USER_GUIDE.md)** - Comprehensive user guide
  - Architecture overview
  - CLI reference (all 27 commands)
  - Tool examples
  - Workflows
  - Safety & approval system
  - Configuration reference
  - RBAC roles
  - Development guide
  - Troubleshooting

### Technical Reference
- **[README.md](./README.md)** - Project overview
  - What's implemented
  - Quickstart examples
  - How to add tools
  - Quality gates

### Specifications
- **[agent-specs/README.md](./agent-specs/README.md)** - Specification overview
  - 00-foundations.md - State store, tool framework, RBAC, audit
  - 01-safety-and-governance.md - Dry-run, approval, policy engine
  - 02-agent-core.md - Planner, executor, memory
  - 03-tool-warewulf.md - Provisioning tools
  - 04-tool-ansible.md - Configuration management
  - 05-tool-slurm.md - Scheduler tools
  - 06-tool-spack.md - System software tools
  - 07-workflows.md - Composite workflows
  - 08-testing.md - Testing & validation

## Command Categories

### Slurm Management (13 tools)
- `qos` - Quality of Service management
- `account` - Account hierarchy
- `assoc` - User associations
- `set-limits` - Resource limits
- `show-assoc` - View associations
- `node-status` - Node status
- `queue` - Job queue
- `job-accounting` - Completed job records
- `usage-report` - Utilization reports
- `diag` - Diagnostics
- `node-state` - Drain/resume/down
- `reservation` - Maintenance reservations
- `reconfigure` - Controller reload

### Warewulf Provisioning (9 tools)
- `import-container` - Import OS images
- `build-node-image` - Build compute images
- `define-profile` - Define node profiles
- `manage-overlay` - Manage overlays
- `assign-image-to-nodes` - Assign images
- `provision-node` - Register new nodes
- `rebuild-overlay` - Rebuild overlays
- `list-images` - List images
- `list-nodes` - List nodes

### Ansible Configuration (5 tools)
- `lint-playbook` - Validate playbooks
- `compose-playbook` - Compose from roles
- `manage-inventory` - Generate inventory
- `run-playbook` - Execute playbooks
- `check-secret` - Verify secrets

### Spack Software (11 tools)
- `spack-envs` - List environments
- `spack-find` - List installed specs
- `spack-spec` - Preview concretization
- `spack-compilers` - Manage compilers
- `spack-env` - Manage environments
- `spack-buildcache` - Buildcache management
- `spack-modules` - Generate modulefiles
- `spack-view` - Create filesystem views
- `spack-install` - Install packages

### Core Functions (2 tools)
- `tools` - List all tools and schemas
- `plan` - Build/execute plans from intent

## Workflow Examples

### Quick Operations
```bash
# Extend user wall time
hpc-agent plan "give alice 48 hours wall time on gpu qos" --apply

# Add new GPU node (requires full provisioning workflow)
# See USER_GUIDE.md for detailed examples

# Rolling update
python3 -m hpc_agent.workflows.rolling_update \
  --group compute_gpu \
  --new-image gpu-rocky9-v2 \
  --batch-size 2 \
  --actor admin
```

### Safety Features
- **Default dry-run**: All mutating tools show changes first
- **Approval gates**: Medium/High risk actions require approval
- **Policy engine**: YAML rules enforce site policies
- **Audit trail**: Complete command history
- **Rollback**: Undo every action

## Architecture

```
Interaction Layer (CLI/HTTP/Chat)
         │
    Agent Core (Planner/Executor)
         │
   Safety Layer (Diff/Policy/Approval)
         │
    Tools Layer (Slurm/Warewulf/Ansible/Spack)
         │
   State Store (PostgreSQL/SQLite)
```

## Development

### Setup
```bash
pip install -e ".[dev]"
mypy hpc_agent/        # Type checking
ruff check hpc_agent   # Linting
pytest                 # Run tests
```

### Adding Tools
See `USER_GUIDE.md` → Development → Adding a New Tool

## Support

- **Issues**: https://github.com/your-org/AutoHPC/issues
- **Discussions**: https://github.com/your-org/AutoHPC/discussions
- **Specs**: `agent-specs/` directory

## License

[Your chosen license here]
