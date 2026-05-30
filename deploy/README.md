# Virtual Cluster Deployment Notes

The `deploy/` directory contains libvirt/QEMU assets for a disposable AutoHPC test
cluster. Use it for integration testing and for validating operational workflows away
from production systems.

The virtual-cluster assets are intentionally small and should be reviewed before use in a
new environment. They may need site-specific storage paths, networking, SSH access, and
base-image adjustments.

## Topology

| VM | Role | File |
|----|------|------|
| `mgmt` | Controller and management node | `vm-mgmt.xml` |
| `login01` | Login node | `vm-login01.xml` |
| `cpu01` | CPU compute node | `vm-cpu01.xml` |
| `gpu01` | GPU compute node with stubbed GPU behavior | `vm-gpu01.xml` |

The network is defined in `network.xml`.

## Files

- `Makefile`: convenience targets for local libvirt workflows.
- `network.xml`: libvirt network definition.
- `vm-*.xml`: VM definitions.
- `scripts/setup-controller.sh`: controller bootstrap helper.

## Prerequisites

- libvirt and `virsh`
- `virt-install`
- SSH access to the VMs after boot
- Sufficient CPU, memory, and disk for the four VMs

On Linux, verify virtualization support before starting:

```bash
virsh --version
virt-install --version
```

## Common Workflow

From the repository root:

```bash
make -f deploy/Makefile up
make -f deploy/Makefile ssh-mgmt
make -f deploy/Makefile down
```

The Makefile is a development convenience, not a production installer. If it does not
match your local libvirt setup, use the XML files directly.

Manual libvirt flow:

```bash
cd deploy
virsh net-define network.xml
virsh net-start hpc-cluster

virsh define vm-mgmt.xml
virsh define vm-login01.xml
virsh define vm-cpu01.xml
virsh define vm-gpu01.xml

virsh start mgmt
virsh start login01
virsh start cpu01
virsh start gpu01
```

## Testing

Unit tests do not require the virtual cluster:

```bash
pytest tests/unit
```

Integration tests are intended to run against the VMs after the controller and services
are configured:

```bash
pytest tests/integration
```

The repository may not always include a complete integration test suite for every
workflow. Treat this directory as the foundation for site-specific validation.

## GPU Stubbing

The `gpu01` VM is intended for GPU workflow validation without requiring physical GPUs.
Set `HPC_GPU_STUB=1` in the guest image or test environment when using stubbed commands
such as `nvidia-smi`.

## Troubleshooting

**VMs do not start**

Check libvirt service status, storage pool paths, and VM names that may already exist.

**Network conflicts**

Inspect `network.xml` and adjust the subnet or bridge for your host.

**PXE or Warewulf boot fails**

Check DHCP/TFTP services on the management VM and verify the Warewulf overlay and profile
configuration.

**Controller setup is incomplete**

Log into `mgmt`, inspect `scripts/setup-controller.sh`, and run the remaining setup steps
manually. The script is a bootstrap helper and may require local adaptation.
