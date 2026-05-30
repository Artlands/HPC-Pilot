"""Bootstrap a bare-metal cluster from scratch (day-0 setup).

See spec 09 §3.
"""

from __future__ import annotations

from hpc_agent.core.plan import Plan, Step


def build(
    mgmt_interface: str,
    dhcp_range_start: str,
    dhcp_range_end: str,
    base_os: str,
    cpu_image_name: str,
    *,
    dhcp_router: str | None = None,
    gpu_image_name: str | None = None,
    gpu_driver_version: str | None = None,
    gpu_cuda_version: str | None = None,
    nfs_exports: list[str] | None = None,
    nfs_network: str | None = None,
    actor: str,
) -> Plan:
    """Create a plan to bootstrap the Warewulf provisioning controller.

    Args:
        mgmt_interface: Management NIC for PXE (e.g. "eth0")
        dhcp_range_start: First DHCP IP for PXE (e.g. "10.1.0.100")
        dhcp_range_end: Last DHCP IP for PXE (e.g. "10.1.0.254")
        base_os: OCI/docker ref for the base OS container (e.g. "docker://rockylinux:9")
        cpu_image_name: Name for the built CPU compute image
        dhcp_router: Optional gateway IP for the PXE network
        gpu_image_name: Name for the GPU image; skips GPU steps when None
        gpu_driver_version: NVIDIA driver version for GPU image
        gpu_cuda_version: CUDA toolkit version for GPU image
        nfs_exports: Paths to export via NFS (default: /home, /scratch, /opt/spack)
        nfs_network: CIDR for NFS access (e.g. "10.1.0.0/24")
        actor: Operator identity
    """
    nfs_exports = nfs_exports or ["/home", "/scratch", "/opt/spack"]
    build_gpu = gpu_image_name is not None

    plan = Plan(
        id="bootstrap-cluster",
        intent="day-0 bare-metal cluster bootstrap via Warewulf",
        actor=actor,
        steps=[],
        state="draft",
    )

    # Step 1: Validate Warewulf is installed on the controller
    plan.steps.append(
        Step(
            id="check-server",
            tool="warewulf.server_status",
            input={},
            depends_on=[],
        )
    )

    # Step 2–4: Configure DHCP / TFTP / NFS (network services)
    plan.steps.append(
        Step(
            id="configure-dhcp",
            tool="warewulf.configure_dhcp",
            input={
                "interface": mgmt_interface,
                "range_start": dhcp_range_start,
                "range_end": dhcp_range_end,
                "router": dhcp_router,
            },
            depends_on=["check-server"],
            critical=True,
        )
    )

    plan.steps.append(
        Step(
            id="configure-tftp",
            tool="warewulf.configure_tftp",
            input={"interface": mgmt_interface},
            depends_on=["check-server"],
            critical=True,
        )
    )

    plan.steps.append(
        Step(
            id="configure-nfs",
            tool="warewulf.configure_nfs",
            input={
                "exports": nfs_exports,
                "network": nfs_network,
            },
            depends_on=["check-server"],
            critical=True,
        )
    )

    # Step 5: Ensure the Warewulf server is started (via Ansible)
    plan.steps.append(
        Step(
            id="start-server",
            tool="ansible.run_playbook",
            input={
                "playbook": "warewulf_server",
                "limit": "controller",
                "extra_vars": {},
            },
            depends_on=["configure-dhcp", "configure-tftp", "configure-nfs"],
            critical=True,
        )
    )

    # Step 6: Import base OS container
    plan.steps.append(
        Step(
            id="import-base-os",
            tool="warewulf.import_container",
            input={
                "name": "base-os",
                "source": base_os,
            },
            depends_on=["start-server"],
            critical=True,
        )
    )

    # Step 7: Build CPU compute image
    plan.steps.append(
        Step(
            id="build-cpu-image",
            tool="warewulf.build_node_image",
            input={
                "name": cpu_image_name,
                "base_image": "base-os",
                "kind": "compute_cpu",
            },
            depends_on=["import-base-os"],
            critical=True,
        )
    )

    # Step 8 (optional): Build GPU compute image
    if build_gpu:
        plan.steps.append(
            Step(
                id="build-gpu-image",
                tool="warewulf.build_node_image",
                input={
                    "name": gpu_image_name,
                    "base_image": "base-os",
                    "kind": "compute_gpu",
                    "nvidia_driver_version": gpu_driver_version,
                    "cuda_version": gpu_cuda_version,
                    "install_dcgm": True,
                },
                depends_on=["import-base-os"],
                critical=True,
            )
        )

    # Step 9: Define CPU default profile
    plan.steps.append(
        Step(
            id="define-cpu-profile",
            tool="warewulf.define_profile",
            input={
                "name": "cpu-default",
                "image": cpu_image_name,
                "system_overlays": ["wwinit", "hosts", "ssh.host_keys"],
                "runtime_overlays": ["hosts", "ssh.authorized_keys", "munge", "slurm"],
            },
            depends_on=["build-cpu-image"],
            critical=True,
        )
    )

    # Step 10 (optional): Define GPU default profile
    if build_gpu:
        plan.steps.append(
            Step(
                id="define-gpu-profile",
                tool="warewulf.define_profile",
                input={
                    "name": "gpu-default",
                    "image": gpu_image_name,
                    "system_overlays": ["wwinit", "hosts", "ssh.host_keys"],
                    "runtime_overlays": ["hosts", "ssh.authorized_keys", "munge", "slurm"],
                },
                depends_on=["build-gpu-image"],
                critical=True,
            )
        )

    # Step 11: Build standard overlays
    last_profile_step = "define-gpu-profile" if build_gpu else "define-cpu-profile"
    plan.steps.append(
        Step(
            id="build-overlays",
            tool="warewulf.rebuild_overlay",
            input={"node": None},  # rebuild all overlays
            depends_on=[last_profile_step],
            critical=True,
        )
    )

    # Step 12: Generate initial Ansible inventory from state store
    plan.steps.append(
        Step(
            id="generate-inventory",
            tool="ansible.manage_inventory",
            input={"regenerate": True},
            depends_on=["build-overlays"],
        )
    )

    return plan
