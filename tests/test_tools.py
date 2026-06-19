"""Tests for HPC Pilot tools module."""
from __future__ import annotations

import subprocess
from unittest.mock import Mock, patch

import pytest


# ---------------------------------------------------------------------------
# Availability probes
# ---------------------------------------------------------------------------


class TestSlurmCheckFunctions:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_check_slurm_available(self, mock_run):
        from hpc_pilot.tools import check_slurm_available

        mock_run.return_value = Mock(returncode=0)
        assert check_slurm_available() is True

    @patch("hpc_pilot.tools.subprocess.run")
    def test_check_slurm_not_available_called_process_error(self, mock_run):
        from hpc_pilot.tools import check_slurm_available

        mock_run.side_effect = subprocess.CalledProcessError(1, "scontrol")
        assert check_slurm_available() is False

    @patch("hpc_pilot.tools.subprocess.run")
    def test_check_slurm_timeout(self, mock_run):
        from hpc_pilot.tools import check_slurm_available

        mock_run.side_effect = subprocess.TimeoutExpired("scontrol", 5)
        assert check_slurm_available() is False

    @patch("hpc_pilot.tools.subprocess.run")
    def test_check_slurm_not_found(self, mock_run):
        from hpc_pilot.tools import check_slurm_available

        mock_run.side_effect = FileNotFoundError
        assert check_slurm_available() is False


# ---------------------------------------------------------------------------
# _run helper
# ---------------------------------------------------------------------------


class TestRunHelper:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_run_returns_stdout_on_success(self, mock_run):
        from hpc_pilot.tools import _run

        mock_run.return_value = Mock(returncode=0, stdout="hello\n", stderr="")
        assert _run(["echo", "hello"]) == "hello\n"

    @patch("hpc_pilot.tools.subprocess.run")
    def test_run_raises_on_nonzero_exit(self, mock_run):
        from hpc_pilot.tools import _run

        mock_run.return_value = Mock(returncode=1, stdout="", stderr="permission denied")
        with pytest.raises(RuntimeError, match="permission denied"):
            _run(["scontrol", "something"])

    def test_run_dry_run_does_not_call_subprocess(self):
        from hpc_pilot.tools import _run

        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            result = _run(["sacctmgr", "modify", "qos", "gpu"], dry_run=True)

        mock_run.assert_not_called()
        assert result.startswith("DRY-RUN:")
        assert "sacctmgr" in result


# ---------------------------------------------------------------------------
# parse_slurm_nodes
# ---------------------------------------------------------------------------


_SCONTROL_TWO_NODES = """\
NodeName=node01 Arch=x86_64 CoresPerSocket=12
   CPUAlloc=0 CPUTot=24 CPULoad=0.01
   NodeAddr=10.0.0.1 NodeHostName=node01
   NodeState=IDLE

NodeName=node02 Arch=x86_64 CoresPerSocket=12
   CPUAlloc=24 CPUTot=24 CPULoad=1.00
   NodeAddr=10.0.0.2 NodeHostName=node02
   NodeState=DOWN
"""


class TestParseSlurmNodes:
    def test_parses_two_nodes(self):
        from hpc_pilot.tools import parse_slurm_nodes

        result = parse_slurm_nodes(_SCONTROL_TWO_NODES)
        assert set(result.keys()) == {"node01", "node02"}

    def test_node_fields_round_trip(self):
        from hpc_pilot.tools import parse_slurm_nodes

        result = parse_slurm_nodes(_SCONTROL_TWO_NODES)
        assert result["node01"]["NodeState"] == "IDLE"
        assert result["node02"]["NodeState"] == "DOWN"
        assert result["node01"]["CPULoad"] == "0.01"

    def test_health_check_detects_down_node(self):
        """hpc_cluster_health_check flags DOWN nodes when scontrol output contains one."""
        from hpc_pilot.tools import hpc_cluster_health_check

        with patch("hpc_pilot.tools.check_slurm_available", return_value=True), \
             patch("hpc_pilot.tools.check_warewulf_available", return_value=False), \
             patch("hpc_pilot.tools.check_spack_available", return_value=False), \
             patch("hpc_pilot.tools.check_ansible_available", return_value=False), \
             patch("hpc_pilot.tools._run", return_value=_SCONTROL_TWO_NODES):

            result = hpc_cluster_health_check()

        assert result["overall"] == "degraded"
        assert any("node02" in issue for issue in result["issues"])

    def test_empty_output(self):
        from hpc_pilot.tools import parse_slurm_nodes

        assert parse_slurm_nodes("") == {}


# ---------------------------------------------------------------------------
# Slurm node status
# ---------------------------------------------------------------------------


class TestSlurmNodeStatus:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_success(self, mock_run):
        from hpc_pilot.tools import hpc_slurm_node_status

        mock_run.return_value = Mock(returncode=0, stdout="NodeName=node01 ...", stderr="")
        assert "NodeName=node01" in hpc_slurm_node_status("node01")

    @patch("hpc_pilot.tools.subprocess.run")
    def test_nonzero_raises(self, mock_run):
        """A non-zero exit from scontrol now raises RuntimeError (not silent empty string)."""
        from hpc_pilot.tools import hpc_slurm_node_status

        mock_run.return_value = Mock(returncode=1, stdout="", stderr="node not found")
        with pytest.raises(RuntimeError, match="scontrol exited 1"):
            hpc_slurm_node_status("nonexistent")

    def test_invalid_node_name_rejected(self):
        """Node names with shell-special chars are rejected before subprocess is called."""
        from hpc_pilot.tools import hpc_slurm_node_status

        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            with pytest.raises(ValueError, match="Invalid node name"):
                hpc_slurm_node_status("--help")
        mock_run.assert_not_called()

    @patch("hpc_pilot.tools.subprocess.run")
    def test_empty_node_queries_all(self, mock_run):
        """Empty node name calls scontrol without the node argument (list all)."""
        from hpc_pilot.tools import hpc_slurm_node_status

        mock_run.return_value = Mock(returncode=0, stdout="NodeName=node01 ...", stderr="")
        hpc_slurm_node_status("")

        call_args = mock_run.call_args[0][0]
        # argv[0] may be a full path from config; check the command stem and sub-args
        assert call_args[0].endswith("scontrol")
        assert call_args[1:] == ["show", "node"]


# ---------------------------------------------------------------------------
# Slurm queue
# ---------------------------------------------------------------------------


class TestSlurmQueue:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_success(self, mock_run):
        from hpc_pilot.tools import hpc_slurm_queue

        mock_run.return_value = Mock(returncode=0, stdout="JOBID PARTITION ...", stderr="")
        assert "JOBID" in hpc_slurm_queue({"user": "alice"})

    @patch("hpc_pilot.tools.subprocess.run")
    def test_filters_passed_to_argv(self, mock_run):
        from hpc_pilot.tools import hpc_slurm_queue

        mock_run.return_value = Mock(returncode=0, stdout="JOBID ...", stderr="")
        hpc_slurm_queue({"user": "alice", "partition": "gpu"})

        argv = mock_run.call_args[0][0]
        assert "--user" in argv
        assert "alice" in argv
        assert "--partition" in argv
        assert "gpu" in argv

    def test_unknown_filter_key_rejected(self):
        from hpc_pilot.tools import hpc_slurm_queue

        with pytest.raises(ValueError, match="Unknown filter key"):
            hpc_slurm_queue({"foo": "bar"})


# ---------------------------------------------------------------------------
# Slurm node state
# ---------------------------------------------------------------------------


class TestSlurmNodeState:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_drain_with_reason(self, mock_run):
        from hpc_pilot.tools import hpc_slurm_node_state

        mock_run.return_value = Mock(returncode=0, stdout="Updated", stderr="")
        result = hpc_slurm_node_state("node01", "drain", "maintenance")

        assert "Updated" in result
        argv = mock_run.call_args[0][0]
        assert "drain" in str(argv)
        assert "maintenance" in str(argv)

    def test_invalid_target_rejected(self):
        from hpc_pilot.tools import hpc_slurm_node_state

        with pytest.raises(ValueError, match="Invalid target state"):
            hpc_slurm_node_state("node01", "explode")

    def test_dry_run_does_not_call_subprocess(self):
        from hpc_pilot.tools import hpc_slurm_node_state

        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            result = hpc_slurm_node_state("node01", "drain", dry_run=True)

        mock_run.assert_not_called()
        assert "DRY-RUN" in result


# ---------------------------------------------------------------------------
# Slurm QOS modify
# ---------------------------------------------------------------------------


class TestSlurmQOSModify:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_success_with_wall(self, mock_run):
        from hpc_pilot.tools import hpc_slurm_qos_modify

        mock_run.return_value = Mock(returncode=0, stdout="Modified", stderr="")
        result = hpc_slurm_qos_modify("gpu", 2880)

        assert "Modified" in result
        assert "MaxWall=2880" in str(mock_run.call_args)

    def test_dry_run_does_not_call_subprocess(self):
        from hpc_pilot.tools import hpc_slurm_qos_modify

        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            result = hpc_slurm_qos_modify("gpu", 60, dry_run=True)

        mock_run.assert_not_called()
        assert "DRY-RUN" in result
        assert "sacctmgr" in result

    @patch("hpc_pilot.tools.subprocess.run")
    def test_no_wall_time(self, mock_run):
        from hpc_pilot.tools import hpc_slurm_qos_modify

        mock_run.return_value = Mock(returncode=0, stdout="Modified", stderr="")
        hpc_slurm_qos_modify("gpu", None)

        argv = mock_run.call_args[0][0]
        assert not any("MaxWall" in a for a in argv)


# ---------------------------------------------------------------------------
# Warewulf
# ---------------------------------------------------------------------------


class TestWarewulfFunctions:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_check_warewulf_available(self, mock_run):
        from hpc_pilot.tools import check_warewulf_available

        mock_run.return_value = Mock(returncode=0)
        assert check_warewulf_available() is True

    @patch("hpc_pilot.tools.subprocess.run")
    def test_node_status(self, mock_run):
        from hpc_pilot.tools import hpc_warewulf_node_status

        mock_run.return_value = Mock(returncode=0, stdout="NODE NAME  STATE", stderr="")
        assert "NODE NAME" in hpc_warewulf_node_status()

    @patch("hpc_pilot.tools.subprocess.run")
    def test_image_list(self, mock_run):
        from hpc_pilot.tools import hpc_warewulf_image_list

        mock_run.return_value = Mock(returncode=0, stdout="IMAGE NAME  SIZE", stderr="")
        assert "IMAGE NAME" in hpc_warewulf_image_list()

    def test_power_reset_dry_run(self):
        from hpc_pilot.tools import hpc_warewulf_power_reset

        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            result = hpc_warewulf_power_reset("node01", dry_run=True)

        mock_run.assert_not_called()
        assert "DRY-RUN" in result
        assert "wwctl" in result
        assert "power" in result
        assert "reset" in result


# ---------------------------------------------------------------------------
# Spack
# ---------------------------------------------------------------------------


class TestSpackFunctions:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_check_spack_available(self, mock_run):
        from hpc_pilot.tools import check_spack_available

        mock_run.return_value = Mock(returncode=0)
        assert check_spack_available() is True

    @patch("hpc_pilot.tools.subprocess.run")
    def test_env_list(self, mock_run):
        from hpc_pilot.tools import hpc_spack_env_list

        mock_run.return_value = Mock(returncode=0, stdout="==> Environments", stderr="")
        assert "Environments" in hpc_spack_env_list()

    @patch("hpc_pilot.tools.subprocess.run")
    def test_find(self, mock_run):
        from hpc_pilot.tools import hpc_spack_find

        mock_run.return_value = Mock(returncode=0, stdout="my-package@1.0", stderr="")
        assert "my-package" in hpc_spack_find("my-env")

    @patch("hpc_pilot.tools.subprocess.run")
    def test_compilers(self, mock_run):
        from hpc_pilot.tools import hpc_spack_compilers

        mock_run.return_value = Mock(returncode=0, stdout="gcc@11.0.0", stderr="")
        assert "gcc" in hpc_spack_compilers()


# ---------------------------------------------------------------------------
# Ansible
# ---------------------------------------------------------------------------


class TestAnsibleFunctions:
    @patch("hpc_pilot.tools.subprocess.run")
    def test_check_ansible_available(self, mock_run):
        from hpc_pilot.tools import check_ansible_available

        mock_run.return_value = Mock(returncode=0)
        assert check_ansible_available() is True

    @patch("hpc_pilot.tools.subprocess.run")
    def test_playbook_run(self, mock_run):
        from hpc_pilot.tools import hpc_ansible_playbook_run

        mock_run.return_value = Mock(returncode=0, stdout="PLAY RECAP", stderr="")
        assert "PLAY RECAP" in hpc_ansible_playbook_run("/path/to/playbook.yml")

    def test_playbook_dry_run(self):
        from hpc_pilot.tools import hpc_ansible_playbook_run

        with patch("hpc_pilot.tools.subprocess.run") as mock_run:
            result = hpc_ansible_playbook_run("/path/to/playbook.yml", dry_run=True)

        mock_run.assert_not_called()
        assert "DRY-RUN" in result
        assert "ansible-playbook" in result

    def test_empty_playbook_rejected(self):
        from hpc_pilot.tools import hpc_ansible_playbook_run

        with pytest.raises(ValueError, match="playbook path must not be empty"):
            hpc_ansible_playbook_run("")

    @patch("hpc_pilot.tools.subprocess.run")
    def test_inventory_generate(self, mock_run):
        from hpc_pilot.tools import hpc_ansible_inventory_generate

        mock_run.return_value = Mock(returncode=0, stdout='{"all": {"hosts": {}}}', stderr="")
        assert "all" in hpc_ansible_inventory_generate()


# ---------------------------------------------------------------------------
# Cluster health check
# ---------------------------------------------------------------------------


class TestClusterHealthCheck:
    @patch("hpc_pilot.tools.check_slurm_available", return_value=True)
    @patch("hpc_pilot.tools.check_warewulf_available", return_value=False)
    @patch("hpc_pilot.tools.check_spack_available", return_value=False)
    @patch("hpc_pilot.tools.check_ansible_available", return_value=False)
    @patch("hpc_pilot.tools._run")
    def test_healthy_cluster(self, mock_run, *_checks):
        from hpc_pilot.tools import hpc_cluster_health_check

        mock_run.return_value = "NodeName=node01 NodeState=IDLE\n"
        result = hpc_cluster_health_check()

        assert "components" in result
        assert "slurm" in result["components"]
        assert result["overall"] == "healthy"

    @patch("hpc_pilot.tools.check_slurm_available", return_value=False)
    @patch("hpc_pilot.tools.check_warewulf_available", return_value=False)
    @patch("hpc_pilot.tools.check_spack_available", return_value=False)
    @patch("hpc_pilot.tools.check_ansible_available", return_value=False)
    def test_no_tools_available(self, *_checks):
        from hpc_pilot.tools import hpc_cluster_health_check

        result = hpc_cluster_health_check()
        assert result["components"]["slurm"]["available"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
