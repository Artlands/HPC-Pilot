# HPC Pilot Design Reference

The files in this directory describe the intended contracts for HPC Pilot components. They
are useful when extending the agent, reviewing behavior, or deciding whether a tool
change preserves the safety model.

These documents are not release notes. For day-to-day usage, start with the top-level
[README](../README.md) for setup, usage, and LLM configuration.

## LLM Support

HPC Pilot includes optional LLM-powered planning:

- **Provider-agnostic interface** (`core/llm.py:LLMProvider`) supports Anthropic, OpenAI,
  or custom backends
- **Safety-aware tool-calling** - all plans go through approval gates
- **Default to CLI mode** - works without LLM for rule-based planning
- **Mock mode** for testing deterministic behavior

See [README.md](../README.md#llm-configuration) for setup instructions.

## Reference Documents

| File | Topic |
|------|-------|
| [00-foundations.md](00-foundations.md) | State store, repositories, config repo, tool framework, command runner, audit, RBAC, settings |
| [01-safety-and-governance.md](01-safety-and-governance.md) | Diffs, approval gates, policy engine, rollback, blast radius |
| [02-agent-core.md](02-agent-core.md) | Plan model, planner, executor, resumable plans, interaction surfaces |
| [03-tool-warewulf.md](03-tool-warewulf.md) | Warewulf provisioning and image-management tools |
| [04-tool-ansible.md](04-tool-ansible.md) | Ansible role composition, inventory, lint, apply, and secret handling |
| [05-tool-slurm.md](05-tool-slurm.md) | Slurm account, QOS, association, node, partition, reservation, and reporting tools |
| [06-tool-spack.md](06-tool-spack.md) | Spack environment, compiler, install, buildcache, module, and view tools |
| [07-workflows.md](07-workflows.md) | Multi-tool workflow plan builders |
| [08-testing.md](08-testing.md) | Unit, integration, virtual-cluster, eval, and CI strategy |
| [09-cluster-bootstrap.md](09-cluster-bootstrap.md) | Day-0 bare-metal controller bootstrap: DHCP, TFTP, NFS setup and cluster bring-up workflow |

## Core Conventions

- Tools use Pydantic input/output models.
- Mutating tools support dry-run and produce a structured diff before execution.
- All shell commands go through the allowlisted command runner.
- RBAC and policy are evaluated before mutation.
- Operations are auditable and include command records.
- Config changes flow through the managed config repository.
- Tool implementations should be idempotent.

## How to Use This Reference

When adding or changing behavior:

1. Read the relevant domain document.
2. Check the foundation and safety contracts.
3. Add focused unit tests for dry-run, policy, apply, errors, and idempotency.
4. Update user-facing docs if a command, workflow, setting, or safety behavior changes.
