"""Tests for Phase 4 Ansible tools."""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


def _mock_cluster(tmp_home: str = "/tmp/.hpc-pilot"):
    cl = MagicMock()
    cl.ansible_playbook.return_value = "ansible-playbook"
    cl.ansible_inventory.return_value = "ansible-inventory"
    cl.warewulf.return_value = "/usr/bin/wwctl"
    cl.slurm.return_value = "/usr/bin/scontrol"
    cl.ansible_dir = os.path.join(tmp_home, "ansible")
    cl.ssh = None
    return cl


def _sp_run_ok(stdout: str = "OK", returncode: int = 0):
    return MagicMock(returncode=returncode, stdout=stdout, stderr="")


# ===================================================================
# hpc_ansible_playbook_check
# ===================================================================


class TestAnsiblePlaybookCheck:
    @patch("hpc_pilot.tools.ansible.subprocess.run")
    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.ansible import hpc_ansible_playbook_check

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = _sp_run_ok(
            json.dumps({"plays": [{"play": {"name": "check"}}], "stats": {"localhost": {"ok": 1}}})
        )
        result = hpc_ansible_playbook_check("site.yml")
        assert isinstance(result, dict)
        assert result["playbook"] == "site.yml"
        assert result["check_mode"] is True
        assert "plays" in result
        assert "stats" in result

    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.ansible import hpc_ansible_playbook_check

        mock_cl.return_value = _mock_cluster()
        result = hpc_ansible_playbook_check("site.yml", dry_run=True)
        assert "dry_run" in result
        assert "DRY-RUN" in result["dry_run"]


# ===================================================================
# hpc_ansible_playbook_list
# ===================================================================


class TestAnsiblePlaybookList:
    def test_playbook_list(self, tmp_home):
        from hpc_pilot.tools.ansible import hpc_ansible_playbook_list

        # Create a playbook directory under tmp_home
        ansible_dir = os.path.join(tmp_home, "ansible")
        playbooks_dir = os.path.join(ansible_dir, "playbooks")
        os.makedirs(playbooks_dir, exist_ok=True)
        for name in ["site.yml", "drift.yml"]:
            with open(os.path.join(playbooks_dir, name), "w") as f:
                f.write(f"# {name} playbook\n")

        cl = _mock_cluster(tmp_home)
        with patch("hpc_pilot.tools.ansible._resolve_cluster", return_value=cl):
            result = hpc_ansible_playbook_list()
        assert len(result) == 2
        names = {r["name"] for r in result}
        assert "site" in names
        assert "drift" in names

    def test_playbook_list_empty_dir(self, tmp_home):
        from hpc_pilot.tools.ansible import hpc_ansible_playbook_list

        ansible_dir = os.path.join(tmp_home, "ansible")
        os.makedirs(os.path.join(ansible_dir, "playbooks"), exist_ok=True)

        cl = _mock_cluster(tmp_home)
        with patch("hpc_pilot.tools.ansible._resolve_cluster", return_value=cl):
            result = hpc_ansible_playbook_list()
        assert result == []


# ===================================================================
# hpc_ansible_role_list
# ===================================================================


class TestAnsibleRoleList:
    def test_role_list(self, tmp_home):
        from hpc_pilot.tools.ansible import hpc_ansible_role_list

        ansible_dir = os.path.join(tmp_home, "ansible")
        roles_dir = os.path.join(ansible_dir, "roles")
        os.makedirs(roles_dir, exist_ok=True)
        for role in ["common", "nvidia", "slurm"]:
            os.makedirs(os.path.join(roles_dir, role))

        cl = _mock_cluster(tmp_home)
        with patch("hpc_pilot.tools.ansible._resolve_cluster", return_value=cl):
            result = hpc_ansible_role_list()
        assert "common" in result
        assert "nvidia" in result
        assert "slurm" in result

    def test_role_list_empty(self, tmp_home):
        from hpc_pilot.tools.ansible import hpc_ansible_role_list

        ansible_dir = os.path.join(tmp_home, "ansible")
        os.makedirs(os.path.join(ansible_dir, "roles"), exist_ok=True)

        cl = _mock_cluster(tmp_home)
        with patch("hpc_pilot.tools.ansible._resolve_cluster", return_value=cl):
            result = hpc_ansible_role_list()
        assert result == []


# ===================================================================
# hpc_ansible_inventory_from_truth
# ===================================================================


class TestAnsibleInventoryFromTruth:
    @patch("hpc_pilot.tools.ansible.subprocess.run")
    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.ansible import hpc_ansible_inventory_from_truth

        cl = _mock_cluster()
        mock_cl.return_value = cl

        # First call: wwctl node list, second call: scontrol show nodes
        mock_run.side_effect = [
            _sp_run_ok("NODE NAME          IPADDR\nnode01           10.0.0.1\nnode02           10.0.0.2\n"),
            _sp_run_ok(
                "NodeName=node01 Features=gpu Partitions=gpu\n"
                "NodeName=node02 Features=cpu Partitions=cpu\n"
            ),
        ]

        result = hpc_ansible_inventory_from_truth()
        assert isinstance(result, dict)
        assert "inventory_path" in result
        assert "gpu_nodes" in result
        assert "cpu_nodes" in result
        assert "node01" in result["gpu_nodes"]
        assert "node02" in result["cpu_nodes"]


# ===================================================================
# hpc_ansible_drift_check
# ===================================================================


class TestAnsibleDriftCheck:
    @patch("hpc_pilot.tools.ansible.subprocess.run")
    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_no_drift_dir(self, mock_cl, mock_run):
        from hpc_pilot.tools.ansible import hpc_ansible_drift_check

        mock_cl.return_value = _mock_cluster()
        # Patch os.path.isdir to return False for the drift dir
        with patch("hpc_pilot.tools.ansible.os.path.isdir", return_value=False):
            result = hpc_ansible_drift_check()
        assert "error" in result
        assert "No drift playbooks" in result["error"]


# ===================================================================
# hpc_ansible_vault_decrypt
# ===================================================================


class TestAnsibleVaultDecrypt:
    @patch("hpc_pilot.tools.ansible.subprocess.run")
    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.ansible import hpc_ansible_vault_decrypt

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = _sp_run_ok("decrypted_secret_data\n")
        result = hpc_ansible_vault_decrypt("/path/to/vault.yml")
        assert "decrypted_secret_data" in result
        cmd = mock_run.call_args[0][0]
        assert "view" in cmd
        assert "/path/to/vault.yml" in cmd

    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.ansible import hpc_ansible_vault_decrypt

        mock_cl.return_value = _mock_cluster()
        result = hpc_ansible_vault_decrypt("/path/to/vault.yml", dry_run=True)
        assert "DRY-RUN" in result


# ===================================================================
# hpc_ansible_run_history
# ===================================================================


class TestAnsibleRunHistory:
    def test_run_history(self, tmp_home):
        from hpc_pilot.paths import get_home
        from hpc_pilot.tools.ansible import hpc_ansible_run_history
        logs_dir = os.path.join(get_home(), "logs", "ansible")
        os.makedirs(logs_dir, exist_ok=True)

        record = {"ts": 1000, "tool": "ansible", "playbook": "site.yml"}
        with open(os.path.join(logs_dir, "run_001.json"), "w") as f:
            json.dump(record, f)

        cl = _mock_cluster(tmp_home)
        with patch("hpc_pilot.tools.ansible._resolve_cluster", return_value=cl):
            result = hpc_ansible_run_history()
        assert len(result) == 1
        assert result[0]["playbook"] == "site.yml"


# ===================================================================
# hpc_ansible_playbook_run (async)
# ===================================================================


class TestAnsiblePlaybookRun:
    @patch("hpc_pilot.jobs.start_job")
    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_async_path(self, mock_cl, mock_start_job):
        from hpc_pilot.tools.ansible import hpc_ansible_playbook_run

        mock_cl.return_value = _mock_cluster()
        mock_record = MagicMock()
        mock_record.run_id = "run_xyz"
        mock_start_job.return_value = mock_record

        result = hpc_ansible_playbook_run("site.yml")
        assert isinstance(result, dict)
        assert result["run_id"] == "run_xyz"

    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.ansible import hpc_ansible_playbook_run

        mock_cl.return_value = _mock_cluster()
        result = hpc_ansible_playbook_run("site.yml", dry_run=True)
        assert "DRY-RUN" in result

    @patch("hpc_pilot.tools.ansible._resolve_cluster")
    def test_empty_playbook_rejected(self, mock_cl):
        from hpc_pilot.tools.ansible import hpc_ansible_playbook_run

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="playbook path"):
            hpc_ansible_playbook_run("")
