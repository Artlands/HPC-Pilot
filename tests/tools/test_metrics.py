"""Tests for Phase 5 Metrics / Observability tools."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


def _mock_cluster():
    cl = MagicMock()
    cl.ssh = None
    cl.slurm.return_value = "/usr/bin/lctl"
    return cl


def _sp_run_ok(stdout: str = ""):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def _sp_run_fail(stderr: str = "error"):
    return MagicMock(returncode=1, stdout="", stderr=stderr)


# ===================================================================
# Prometheus
# ===================================================================


class TestPrometheusQuery:
    @patch("hpc_pilot.tools.metrics.urllib.request.urlopen")
    @patch("hpc_pilot.tools.metrics._cluster_prometheus_url")
    def test_happy_path(self, mock_url, mock_urlopen):
        from hpc_pilot.tools.metrics import hpc_metrics_prometheus_query

        mock_url.return_value = "http://prometheus:9090"
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "success", "data": {"result": []}}).encode()
        mock_urlopen.return_value = mock_resp

        result = hpc_metrics_prometheus_query("up")
        assert result["status"] == "success"

    @patch("hpc_pilot.tools.metrics.urllib.request.urlopen")
    @patch("hpc_pilot.tools.metrics._cluster_prometheus_url")
    def test_range_query(self, mock_url, mock_urlopen):
        from hpc_pilot.tools.metrics import hpc_metrics_prometheus_query

        mock_url.return_value = "http://prometheus:9090"
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "success"}).encode()
        mock_urlopen.return_value = mock_resp

        hpc_metrics_prometheus_query("up", start="2026-01-01T00:00:00Z", end="2026-01-02T00:00:00Z")
        called_url = mock_urlopen.call_args[0][0]
        assert "query_range" in str(called_url)


class TestPrometheusAlerts:
    @patch("hpc_pilot.tools.metrics.urllib.request.urlopen")
    @patch("hpc_pilot.tools.metrics._cluster_prometheus_url")
    def test_happy_path(self, mock_url, mock_urlopen):
        from hpc_pilot.tools.metrics import hpc_metrics_prometheus_alerts

        mock_url.return_value = "http://prometheus:9090"
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(
            {"data": {"alerts": [{"labels": {"alertname": "HighCPU"}}]}}
        ).encode()
        mock_urlopen.return_value = mock_resp

        result = hpc_metrics_prometheus_alerts()
        assert len(result) == 1
        assert result[0]["labels"]["alertname"] == "HighCPU"


# ===================================================================
# GPU: nvidia-smi
# ===================================================================


_NVIDIA_SMI_XML = """\
<?xml version="1.0" ?>
<nvidia_smi_log driver_version="550.54.15">
  <cuda_version>12.4</cuda_version>
  <gpu id="0000:00:01.0">
    <product_name>NVIDIA A100</product_name>
    <temperature>
      <gpu_temp>42 C</gpu_temp>
    </temperature>
    <utilization>
      <gpu_util>75 %</gpu_util>
    </utilization>
    <ecc_errors>
      <volatile>
        <single_bit>0</single_bit>
        <double_bit>0</double_bit>
      </volatile>
      <aggregate>
        <single_bit>5</single_bit>
        <double_bit>0</double_bit>
      </aggregate>
    </ecc_errors>
  </gpu>
</nvidia_smi_log>
"""


class TestGpuNvidiaSmi:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_parse_xml_output(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_gpu_nvidia_smi

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = _NVIDIA_SMI_XML

        result = hpc_gpu_nvidia_smi("node01")
        assert isinstance(result, dict)
        assert result["node"] == "node01"
        assert result["driver_version"] == "550.54.15"
        assert result["cuda_version"] == "12.4"
        assert len(result["gpus"]) == 1
        gpu = result["gpus"][0]
        assert gpu["product_name"] == "NVIDIA A100"
        assert gpu["temperature"] == "42 C"
        assert gpu["gpu_util"] == "75 %"
        assert gpu["ecc_errors"]["volatile_single_bit"] == "0"
        assert gpu["ecc_errors"]["aggregate_single_bit"] == "5"


# ===================================================================
# Storage
# ===================================================================


_MOUNT_OUTPUT = """\
/dev/sda1 on / type ext4 (rw,relatime)
tmpfs on /tmp type tmpfs (rw,nosuid)
"""

_DF_OUTPUT = """\
Filesystem      Size  Used Avail Use% Mounted on
/dev/sda1        50G   20G   30G  40% /
tmpfs            16G     0   16G   0% /tmp
"""


class TestStorageMounts:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_storage_mounts

        mock_cl.return_value = _mock_cluster()

        def run_side_effect(cmd, **kw):
            joined = " ".join(cmd)
            if "mount" in joined and "df" not in joined:
                return _MOUNT_OUTPUT
            if "df" in joined:
                return _DF_OUTPUT
            return ""
        mock_run.side_effect = run_side_effect

        result = hpc_storage_mounts()
        assert isinstance(result, dict)
        assert "mounts" in result
        assert "disk_usage" in result
        assert len(result["mounts"]) == 2
        assert result["mounts"][0]["device"] == "/dev/sda1"
        assert len(result["disk_usage"]) == 2


class TestStorageLustre:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_lustre_status_healthy(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_storage_lustre_status

        mock_cl.return_value = _mock_cluster()

        def run_side_effect(cmd, **kw):
            joined = " ".join(cmd)
            if "obdfilter" in joined:
                return "obdfilter.test-OST0000.state=online\nobdfilter.test-OST0001.state=online\n"
            if "mdt" in joined:
                return "mdt.test-MDT0000.state=active\n"
            return ""
        mock_run.side_effect = run_side_effect

        result = hpc_storage_lustre_status()
        assert result["status"] == "healthy"
        assert len(result["osts"]) == 2
        assert len(result["mdts"]) == 1


# ===================================================================
# Fabric: IB link status
# ===================================================================


_IBSTATUS_OUTPUT = """\
Infiniband device 'mlx5_0' port 1 status:
        state: 4: ACTIVE
        phys_state: 5: LinkUp
        rate: 200 Gb/sec (4X HDR)
"""


class TestFabricIbLinkStatus:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_fabric_ib_link_status

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = _IBSTATUS_OUTPUT

        result = hpc_fabric_ib_link_status("node01")
        assert isinstance(result, dict)
        assert result["node"] == "node01"
        assert len(result.get("links", [])) > 0
        assert len(result["links"]) == 1
        link = result["links"][0]
        assert link["device"] == "mlx5_0"
        assert link["rate"] == "200 Gb/sec (4X HDR)"
        assert "ACTIVE" in link["state"]


# ===================================================================
# Logs
# ===================================================================


class TestLogsSlurmctldTail:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_logs_slurmctld_tail

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "line1\nline2\nline3\n"
        result = hpc_logs_slurmctld_tail(lines=3)
        assert "line1" in result
        cmd = mock_run.call_args[0][0]
        assert "tail" in cmd
        assert any("slurmctld.log" in part for part in cmd)


class TestLogsSearch:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_logs_search

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "Jun 19 12:00:00 host error: oom\n"
        result = hpc_logs_search("oom")
        assert "oom" in result

    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_no_matching_lines(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_logs_search

        mock_cl.return_value = _mock_cluster()
        mock_run.side_effect = RuntimeError("exited 1")
        result = hpc_logs_search("nonexistent")
        assert "(no matching lines)" in result


class TestLogsDmesgXid:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_logs_dmesg_xid

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "[12345.678901] NVRM: Xid (PCI:0000:00:01.0): 79, pid='<unknown>', name='<unknown>', GPU has fallen off the bus.\n"
        result = hpc_logs_dmesg_xid("node01")
        assert len(result) == 1
        assert result[0]["timestamp"] == "12345.678901"
        assert "Xid" in result[0]["message"]


# ===================================================================
# Redaction
# ===================================================================


class TestRedaction:
    def test_never_leaks_password(self):
        from hpc_pilot.tools.metrics import _redact_log_line

        line = 'password=supersecret token=abc123'
        redacted = _redact_log_line(line)
        assert "supersecret" not in redacted
        assert "abc123" not in redacted
        assert "**REDACTED**" in redacted

    def test_never_leaks_api_key(self):
        from hpc_pilot.tools.metrics import _redact_log_line

        line = 'api_key=sk-1234567890abcdef'
        redacted = _redact_log_line(line)
        assert "sk-1234567890abcdef" not in redacted
        assert "**REDACTED**" in redacted

    def test_redact_email(self):
        from hpc_pilot.tools.metrics import _redact_log_line

        line = 'user@example.com logged in'
        redacted = _redact_log_line(line)
        assert "user@example.com" not in redacted
        assert "EMAIL-REDACTED" in redacted

    def test_redact_output_summarizes_large_output(self):
        from hpc_pilot.tools.metrics import _redact_output

        # Create >10KB content with repeated lines
        large = ("line content repeated often\n" * 800) + ("unique pattern\n" * 50)
        result = _redact_output(large)
        assert "truncated" in result
        assert "top-5" in result
        # Verify patterns appear: 800x of "line content repeated often"
        # and 50x of "unique pattern" (possibly counted differently due to stripping)
        assert "800x" in result or "850x" in result


# ===================================================================
# GPU DCGM
# ===================================================================


class TestGpuDcgmDiag:
    @patch("hpc_pilot.tools.metrics._run")
    @patch("hpc_pilot.tools.metrics._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.metrics import hpc_gpu_dcgm_diag

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "DCGM diagnostic completed successfully"
        result = hpc_gpu_dcgm_diag("node01")
        assert "DCGM" in result
        cmd = mock_run.call_args[0][0]
        assert "dcgmi" in cmd
