# VM Definition Reference

This directory contains libvirt XML definitions for the AutoHPC virtual test cluster.

## VM Roles

| VM | Purpose | Definition |
|----|---------|------------|
| `mgmt` | Slurm/Warewulf/Ansible/Spack controller | `vm-mgmt.xml` |
| `login01` | User-facing login node | `vm-login01.xml` |
| `cpu01` | CPU compute node | `vm-cpu01.xml` |
| `gpu01` | GPU workflow node with stubbed GPU behavior | `vm-gpu01.xml` |

## Network

`network.xml` defines the libvirt network used by the VMs. Review the subnet and bridge
settings before starting the cluster on a shared workstation.

## Manual Usage

```bash
cd deploy

virsh net-define network.xml
virsh net-start hpc-cluster
virsh net-autostart hpc-cluster

virsh define vm-mgmt.xml
virsh define vm-login01.xml
virsh define vm-cpu01.xml
virsh define vm-gpu01.xml

virsh start mgmt
virsh start login01
virsh start cpu01
virsh start gpu01
```

Stop and remove the VMs when finished:

```bash
virsh shutdown gpu01
virsh shutdown cpu01
virsh shutdown login01
virsh shutdown mgmt
```

Use `virsh destroy` and `virsh undefine` only when you intentionally want to force-stop
or remove definitions.

## Notes for Developers

- Keep VM XML definitions small and reviewable.
- Avoid embedding local absolute paths unless they are clearly documented.
- Prefer changes that make the virtual cluster easier to reproduce in CI.
- Keep GPU behavior stub-friendly so tests can run on hosts without physical GPUs.
