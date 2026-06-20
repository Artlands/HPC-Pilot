"""GPU diagnostic tools — nvidia-smi and DCGM via SSH."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _validate
from hpc_pilot.tools.metrics.prometheus import _build_ssh_cmd


@hpc_tool(
    name="hpc_gpu_nvidia_smi",
    role=Role.OPERATOR,
    schema={
        "name": "hpc_gpu_nvidia_smi",
        "description": "Run nvidia-smi -q -x on a compute node via SSH and return per-GPU metrics (temp, util, ECC errors).",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Compute node name"}},
            "required": ["node"],
        },
    },
)
def hpc_gpu_nvidia_smi(
    node: str,
    *,
    cluster: str = "default",
) -> dict[str, Any]:
    """Run nvidia-smi -q -x on a node via SSH and return per-GPU metrics."""
    _validate(node, "node")
    cl = _resolve_cluster(cluster)
    remote_cmd = ["nvidia-smi", "-q", "-x"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)
    output = _run(cmd, timeout=120)

    result: dict[str, Any] = {
        "node": node,
        "gpus": [],
        "driver_version": "",
        "cuda_version": "",
    }

    try:
        root = ET.fromstring(output)
    except ET.ParseError as exc:
        result["error"] = f"Failed to parse nvidia-smi XML output: {exc}"
        return result

    driver = root.attrib.get("driver_version", "")
    cuda_ver = ""
    cuda_elem = root.find("cuda_version")
    if cuda_elem is not None and cuda_elem.text:
        cuda_ver = cuda_elem.text.strip()
    result["driver_version"] = driver
    result["cuda_version"] = cuda_ver

    for gpu_elem in root.findall("gpu"):
        gpu_id = gpu_elem.attrib.get("id", "?")
        product = ""
        pn = gpu_elem.find("product_name")
        if pn is not None and pn.text:
            product = pn.text.strip()

        temp = ""
        temp_elem = gpu_elem.find("temperature/gpu_temp")
        if temp_elem is not None and temp_elem.text:
            temp = temp_elem.text.strip()

        util = ""
        util_elem = gpu_elem.find("utilization/gpu_util")
        if util_elem is not None and util_elem.text:
            util = util_elem.text.strip()

        ecc_errors: dict[str, Any] = {
            "volatile_single_bit": "N/A",
            "volatile_double_bit": "N/A",
            "aggregate_single_bit": "N/A",
            "aggregate_double_bit": "N/A",
        }
        ecc = gpu_elem.find("ecc_errors")
        if ecc is not None:
            for category in ("volatile", "aggregate"):
                cat_elem = ecc.find(category)
                if cat_elem is not None:
                    for label, key in [("single_bit", "single_bit"), ("double_bit", "double_bit")]:
                        elem = cat_elem.find(label)
                        if elem is not None and elem.text:
                            ecc_errors[f"{category}_{key}"] = elem.text.strip()

        result["gpus"].append(
            {
                "gpu_id": gpu_id,
                "product_name": product,
                "temperature": temp,
                "gpu_util": util,
                "ecc_errors": ecc_errors,
            }
        )

    return result


@hpc_tool(
    name="hpc_gpu_dcgm_diag",
    role=Role.ADMIN,
    schema={
        "name": "hpc_gpu_dcgm_diag",
        "description": "Run dcgmi diag -r 1 (level-1 GPU diagnostic) on a node via SSH. Requires admin.",
        "input_schema": {
            "type": "object",
            "properties": {"node": {"type": "string", "description": "Compute node name"}},
            "required": ["node"],
        },
    },
)
def hpc_gpu_dcgm_diag(
    node: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Run dcgmi diag -r 1 on a node via SSH."""
    _validate(node, "node")
    cl = _resolve_cluster(cluster)
    remote_cmd = ["dcgmi", "diag", "-r", "1"]
    cmd = _build_ssh_cmd(node, cl, remote_cmd)
    return _run(cmd, timeout=300, dry_run=dry_run)
