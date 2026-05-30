# AutoHPC Virtual Cluster - VM Definition

VM definitions for libvirt/QEMU.

## VM Specifications

### mgmt (Controller)
- CPUs: 8
- Memory: 16384 MB
- Disk: 100 GB
- Network: virbr0 (192.168.122.0/24)
- Services: slurmctld, slurmdbd, mariadb, warewulf server, ansible control, spack root

### login01 (Login Node)
- CPUs: 4
- Memory: 8192 MB
- Disk: 50 GB
- Network: virbr0
- Services: SSH, common system services

### cpu01 (Compute CPU)
- CPUs: 4
- Memory: 8192 MB
- Disk: 50 GB
- Network: virbr0
- Boot: PXE ( warewulf)

### gpu01 (Compute GPU)
- CPUs: 4
- Memory: 8192 MB
- Disk: 50 GB
- Network: virbr0
- Boot: PXE (warewulf)
- GPU: Virtual (stubbed via HPC_GPU_STUB=1)

## Files

- `vm-mgmt.xml` - Management node definition
- `vm-login01.xml` - Login node definition
- `vm-cpu01.xml` - CPU compute node definition
- `vm-gpu01.xml` - GPU compute node definition
- `network.xml` - Virtual network definition

## Usage

```bash
# Create the network
virsh net-define network.xml
virsh net-start hpc-cluster
virsh net-autostart hpc-cluster

# Create VMs
virsh define vm-mgmt.xml
virsh define vm-login01.xml
virsh define vm-cpu01.xml
virsh define vm-gpu01.xml

# Start VMs
virsh start mgmt
virsh start login01
virsh start cpu01
virsh start gpu01
```
