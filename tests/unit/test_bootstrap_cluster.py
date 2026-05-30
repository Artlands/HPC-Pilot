"""Tests for bootstrap_cluster workflow (spec 09 §3)."""

from __future__ import annotations

from hpc_agent.workflows.bootstrap_cluster import build


def _base_args(**overrides: object) -> dict:
    defaults: dict = {
        "mgmt_interface": "eth0",
        "dhcp_range_start": "10.1.0.100",
        "dhcp_range_end": "10.1.0.254",
        "base_os": "docker://rockylinux:9",
        "cpu_image_name": "rockylinux9-cpu",
        "actor": "alice",
    }
    defaults.update(overrides)
    return defaults


def test_cpu_only_plan_step_count() -> None:
    plan = build(**_base_args())
    # check-server + 3 configure + start + import + build-cpu + define-cpu + overlays + inventory
    assert len(plan.steps) == 10


def test_gpu_plan_adds_two_extra_steps() -> None:
    plan = build(
        **_base_args(
            gpu_image_name="rockylinux9-gpu",
            gpu_driver_version="550.90.07",
        )
    )
    # + build-gpu + define-gpu
    assert len(plan.steps) == 12


def test_step_ids_are_unique() -> None:
    plan = build(**_base_args())
    ids = [s.id for s in plan.steps]
    assert len(ids) == len(set(ids))


def test_dependency_order_cpu_path() -> None:
    plan = build(**_base_args())
    step_map = {s.id: s for s in plan.steps}

    assert "check-server" in step_map["configure-dhcp"].depends_on
    assert "check-server" in step_map["configure-tftp"].depends_on
    assert "check-server" in step_map["configure-nfs"].depends_on
    assert "start-server" in step_map["import-base-os"].depends_on
    assert "import-base-os" in step_map["build-cpu-image"].depends_on
    assert "build-cpu-image" in step_map["define-cpu-profile"].depends_on
    assert "define-cpu-profile" in step_map["build-overlays"].depends_on
    assert "build-overlays" in step_map["generate-inventory"].depends_on


def test_no_gpu_steps_in_cpu_only_plan() -> None:
    plan = build(**_base_args())
    ids = {s.id for s in plan.steps}
    assert "build-gpu-image" not in ids
    assert "define-gpu-profile" not in ids


def test_gpu_steps_present_when_requested() -> None:
    plan = build(**_base_args(gpu_image_name="rockylinux9-gpu"))
    ids = {s.id for s in plan.steps}
    assert "build-gpu-image" in ids
    assert "define-gpu-profile" in ids


def test_plan_intent_and_actor() -> None:
    plan = build(**_base_args(actor="bob"))
    assert plan.actor == "bob"
    assert "bootstrap" in plan.intent.lower()


def test_gpu_image_depends_on_import() -> None:
    plan = build(**_base_args(gpu_image_name="rockylinux9-gpu"))
    step_map = {s.id: s for s in plan.steps}
    assert "import-base-os" in step_map["build-gpu-image"].depends_on


def test_gpu_last_profile_feeds_overlays() -> None:
    plan = build(**_base_args(gpu_image_name="rockylinux9-gpu"))
    step_map = {s.id: s for s in plan.steps}
    assert "define-gpu-profile" in step_map["build-overlays"].depends_on


def test_gateway_routed_to_profile_not_dhcp() -> None:
    plan = build(**_base_args(gateway="192.168.122.1"))
    step_map = {s.id: s for s in plan.steps}
    # Gateway belongs on the node profile network, not the DHCP service.
    assert "router" not in step_map["configure-dhcp"].input
    assert step_map["define-cpu-profile"].input["network"]["gateway"] == "192.168.122.1"


def test_configure_step_inputs_have_no_fabricated_flags() -> None:
    plan = build(**_base_args(controller_ip="192.168.122.10"))
    step_map = {s.id: s for s in plan.steps}
    dhcp_in = step_map["configure-dhcp"].input
    assert dhcp_in["range_start"] == "10.1.0.100"
    assert dhcp_in["controller_ip"] == "192.168.122.10"
    assert step_map["configure-tftp"].input == {"enabled": True}
    assert step_map["configure-nfs"].input == {"exports": ["/home", "/scratch", "/opt/spack"]}


def test_critical_flags_set() -> None:
    plan = build(**_base_args())
    step_map = {s.id: s for s in plan.steps}
    for step_id in ["configure-dhcp", "build-cpu-image", "define-cpu-profile"]:
        assert step_map[step_id].critical is True
