# AutoHPC Virtual Cluster

A disposable HPC cluster for end-to-end testing of the agent.

## Overview

The virtual cluster runs on libvirt/QEMU VMs and provides:

| VM | Role | Configuration | Notes |
|----|------|---------------|-------|
| `mgmt` | Controller | slurmctld, slurmdbd, mariadb, warewulf server, ansible control, spack root | Management node |
| `login01` | Login | User-facing login node | SSH access for testing |
| `cpu01` | Compute CPU | PXE-booted compute node | CPU image testing |
| `gpu01` | Compute GPU | PXE-booted compute node | GPU image testing (stubbed) |

## Prerequisites

- macOS or Linux
- Virtualization support (KVM on Linux, nested KVM/Hyper-V on macOS)
- `virsh` and `virt-install` available
- Network access for container imports

## Quick Start

```bash
# Bring up the virtual cluster
make up

# Wait for VMs to boot and provision (~10-15 minutes)
# Login to mgmt node:
make ssh-mgmt

# Run tests
make test

# Teardown
make down
```

## Detailed Usage

### Bring up VMs

```bash
make up
```

This will:
1. Create libvirt network and storage pool
2. Provision mgmt, login01, cpu01, gpu01 VMs
3. Install base OS (Rocky Linux 9) on each
4. Configure mgmt as controller
5. Seed the state store

### Login to VMs

```bash
# Login to mgmt (controller)
make ssh-mgmt

# Login to login node
make ssh-login

# Login to compute nodes
make ssh-cpu01
make ssh-gpu01
```

### Run Tests

```bash
# Unit tests
make test-unit

# Integration tests (requires VMs up)
make test-integration

# Full test suite
make test-all
```

### Test Scenarios

The virtual cluster supports testing:

1. **Node Provisioning**: Provision a node, PXE-boot, join cluster
2. **Image Builds**: Build CPU and GPU images (GPU uses stubs)
3. **User Management**: Onboard users, assign QOS, submit jobs
4. **Allocation Extensions**: Extend wall time, TRES limits
5. **Node Maintenance**: Drain, patch, rebuild, resume
6. **Spack Environments**: Create environments, install packages
7. **Ansible Playbooks**: Apply configurations, generate inventories

### GPU Stubbing

For CI environments without GPUs, `gpu01` uses stubbed GPU binaries:

```bash
# On gpu01, nvidia-smi returns stub output
make ssh-gpu01
nvidia-smi  # Shows stub, not real GPU
```

The stubs are selected via `HPC_GPU_STUB=1` environment variable.

## Configuration

The cluster is defined in `deploy/vms.yaml`:

```yaml
vm_templates:
  mgmt:
    cpus: 8
    memory: 16384  # MB
    disk: 100  # GB
    network: virbr0
  
  compute:
    cpus: 4
    memory: 8192  # MB
    disk: 50  # GB
    network: virbr0
```

## Troubleshooting

### VMs won't start

Check virtualization support:

```bash
# Linux
kvm-ok

# macOS (nested)
sysctl -a | grep hv_support
```

### PXE boot fails

Check DHCP and TFTP:

```bash
# On mgmt node
sudo systemctl status dnsmasq
sudo systemctl status tftp
```

### State store mismatch

Re-seed the state store:

```bash
make seed-state
```

## shutdown

```bash
make down
```

This will gracefully shutdown all VMs and cleanup resources.

For development and testing questions, see `tests/README.md`.
