"""
Tests for hpc_pilot/rbac.py and hpc_pilot/audit.py.

Covers the P5 acceptance criteria from IMPROVEMENT_PLAN.md:
  - VIEWER calling a mutating tool raises PermissionError
  - Every invocation appends one line to audit.jsonl
  - --apply without --yes prompts on stdin
"""
from __future__ import annotations

import argparse
import json
import os
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# rbac.py
# ---------------------------------------------------------------------------


class TestRole:
    def test_ordering(self):
        from hpc_pilot.rbac import Role

        assert Role.ADMIN >= Role.OPERATOR
        assert Role.OPERATOR >= Role.VIEWER
        assert Role.ADMIN >= Role.VIEWER
        assert not (Role.VIEWER >= Role.ADMIN)
        assert not (Role.OPERATOR >= Role.ADMIN)

    def test_equality(self):
        from hpc_pilot.rbac import Role

        assert Role.VIEWER >= Role.VIEWER
        assert Role.ADMIN >= Role.ADMIN


class TestCheckPermission:
    def test_viewer_denied_admin_tool(self):
        from hpc_pilot.rbac import check_permission, Role

        with pytest.raises(PermissionError, match="requires role 'admin'"):
            check_permission("hpc_slurm_qos_modify", Role.VIEWER)

    def test_viewer_denied_ansible(self):
        from hpc_pilot.rbac import check_permission, Role

        with pytest.raises(PermissionError, match="requires role 'admin'"):
            check_permission("hpc_ansible_playbook_run", Role.VIEWER)

    def test_operator_denied_admin_tool(self):
        from hpc_pilot.rbac import check_permission, Role

        with pytest.raises(PermissionError, match="requires role 'admin'"):
            check_permission("hpc_slurm_qos_modify", Role.OPERATOR)

    def test_admin_allowed_all(self):
        from hpc_pilot.rbac import check_permission, Role

        # Should not raise
        check_permission("hpc_slurm_qos_modify", Role.ADMIN)
        check_permission("hpc_ansible_playbook_run", Role.ADMIN)
        check_permission("hpc_slurm_node_status", Role.ADMIN)

    def test_viewer_allowed_read_tools(self):
        from hpc_pilot.rbac import check_permission, Role

        for tool in ("hpc_slurm_node_status", "hpc_slurm_queue", "hpc_cluster_health_check",
                     "hpc_warewulf_node_status", "hpc_spack_env_list"):
            check_permission(tool, Role.VIEWER)  # must not raise

    def test_operator_allowed_node_state(self):
        from hpc_pilot.rbac import check_permission, Role

        check_permission("hpc_slurm_node_state", Role.OPERATOR)  # must not raise


class TestGetRole:
    def test_env_var_sets_role(self):
        from hpc_pilot.rbac import get_role, Role

        os.environ["HPC_PILOT_ROLE"] = "admin"
        try:
            assert get_role() == Role.ADMIN
        finally:
            del os.environ["HPC_PILOT_ROLE"]

    def test_env_var_operator(self):
        from hpc_pilot.rbac import get_role, Role

        os.environ["HPC_PILOT_ROLE"] = "operator"
        try:
            assert get_role() == Role.OPERATOR
        finally:
            del os.environ["HPC_PILOT_ROLE"]

    def test_default_is_viewer(self):
        from hpc_pilot.rbac import get_role, Role

        if "HPC_PILOT_ROLE" in os.environ:
            del os.environ["HPC_PILOT_ROLE"]

        with patch("hpc_pilot.rbac.os.path.exists", return_value=False):
            assert get_role() == Role.VIEWER

    def test_auth_json_sets_role(self, tmp_path):
        from hpc_pilot.rbac import get_role, Role

        if "HPC_PILOT_ROLE" in os.environ:
            del os.environ["HPC_PILOT_ROLE"]

        auth_file = tmp_path / "auth.json"
        auth_file.write_text(json.dumps({"role": "operator"}))

        with patch("hpc_pilot.rbac.auth_path", return_value=str(auth_file)):
            role = get_role()

        assert role == Role.OPERATOR

    def test_auth_json_invalid_falls_back_to_viewer(self, tmp_path):
        from hpc_pilot.rbac import get_role, Role

        if "HPC_PILOT_ROLE" in os.environ:
            del os.environ["HPC_PILOT_ROLE"]

        auth_file = tmp_path / "auth.json"
        auth_file.write_text("not json {{{")

        with patch("hpc_pilot.rbac.auth_path", return_value=str(auth_file)):
            role = get_role()

        assert role == Role.VIEWER


# ---------------------------------------------------------------------------
# audit.py
# ---------------------------------------------------------------------------


class TestLogAudit:
    def test_writes_one_jsonl_line(self, tmp_path):
        from hpc_pilot.audit import log_audit, AuditEvent

        audit_file = tmp_path / "audit.jsonl"
        event = AuditEvent(
            tool="hpc_slurm_node_status",
            actor="testuser",
            role="viewer",
            args={"node": "n01"},
            dry_run=False,
        )

        with patch("hpc_pilot.audit.audit_log_path", return_value=str(audit_file)):
            log_audit(event)

        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["tool"] == "hpc_slurm_node_status"
        assert record["actor"] == "testuser"
        assert record["role"] == "viewer"
        assert record["dry_run"] is False
        assert "ts" in record
        assert "duration_ms" in record

    def test_appends_multiple_lines(self, tmp_path):
        from hpc_pilot.audit import log_audit, AuditEvent

        audit_file = tmp_path / "audit.jsonl"
        with patch("hpc_pilot.audit.audit_log_path", return_value=str(audit_file)):
            for i in range(3):
                log_audit(AuditEvent(
                    tool=f"tool_{i}", actor="u", role="admin", args={}, dry_run=False
                ))

        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 3

    def test_secrets_redacted(self, tmp_path):
        from hpc_pilot.audit import log_audit, AuditEvent

        audit_file = tmp_path / "audit.jsonl"
        event = AuditEvent(
            tool="hpc_test",
            actor="u",
            role="admin",
            args={"ANTHROPIC_API_KEY": "sk-secret", "node": "n01", "TELEGRAM_TOKEN": "abc"},
            dry_run=False,
        )

        with patch("hpc_pilot.audit.audit_log_path", return_value=str(audit_file)):
            log_audit(event)

        record = json.loads(audit_file.read_text().strip())
        assert record["args"]["ANTHROPIC_API_KEY"] == "***"
        assert record["args"]["TELEGRAM_TOKEN"] == "***"
        assert record["args"]["node"] == "n01"  # non-secret preserved

    def test_io_error_silently_dropped(self):
        from hpc_pilot.audit import log_audit, AuditEvent

        event = AuditEvent(tool="t", actor="u", role="viewer", args={}, dry_run=False)
        with patch("hpc_pilot.audit.audit_log_path", return_value="/nonexistent/path/audit.jsonl"):
            log_audit(event)  # must not raise


class TestAuditToolContextManager:
    def test_success_writes_one_line(self, tmp_path):
        from hpc_pilot.audit import audit_tool

        audit_file = tmp_path / "audit.jsonl"
        with patch("hpc_pilot.audit.audit_log_path", return_value=str(audit_file)):
            with audit_tool("hpc_slurm_queue", "user1", "viewer", {}, dry_run=True):
                pass

        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["tool"] == "hpc_slurm_queue"
        assert record["dry_run"] is True
        assert record["returncode"] == 0

    def test_exception_writes_error_and_reraises(self, tmp_path):
        from hpc_pilot.audit import audit_tool

        audit_file = tmp_path / "audit.jsonl"
        with patch("hpc_pilot.audit.audit_log_path", return_value=str(audit_file)):
            with pytest.raises(RuntimeError, match="sacctmgr exited 1"):
                with audit_tool("hpc_slurm_qos_modify", "admin1", "admin", {}, dry_run=False):
                    raise RuntimeError("sacctmgr exited 1: permission denied")

        lines = audit_file.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["returncode"] == 1
        assert "sacctmgr" in record["error"]


# ---------------------------------------------------------------------------
# CLI: --apply without --yes prompts on stdin (P5 §8.3)
# ---------------------------------------------------------------------------


class TestApplyPrompt:
    def test_qos_apply_without_yes_prompts_and_aborts_on_no(self):
        """--apply without --yes calls _confirm; user says N → aborted, dispatch not called."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=True, yes=False)

        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.cli.get_role", return_value=Role.ADMIN), \
             patch("hpc_pilot.cli._confirm", return_value=False) as mock_confirm, \
             patch("hpc_pilot.dispatch.invoke") as mock_invoke:
            result = qos_command(args)

        mock_confirm.assert_called_once()
        mock_invoke.assert_not_called()
        assert result == 0  # aborted cleanly, not an error

    def test_qos_apply_without_yes_prompts_and_executes_on_yes(self):
        """--apply without --yes calls _confirm; user says Y → dispatch executes."""
        from hpc_pilot.cli import qos_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(name="gpu", max_wall_min=60, apply=True, yes=False)

        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.cli.get_role", return_value=Role.ADMIN), \
             patch("hpc_pilot.cli._confirm", return_value=True), \
             patch("hpc_pilot.dispatch.invoke", return_value="Modified") as mock_invoke:
            result = qos_command(args)

        mock_invoke.assert_called_once()
        assert result == 0

    def test_ansible_apply_without_yes_prompts(self):
        """ansible --apply without --yes also prompts."""
        from hpc_pilot.cli import ansible_command
        from hpc_pilot.rbac import Role

        args = argparse.Namespace(playbook="/path/play.yml", limit=None,
                                  apply=True, yes=False, check=False)

        with patch("hpc_pilot.cli.ensure_home"), \
             patch("hpc_pilot.cli.get_role", return_value=Role.ADMIN), \
             patch("hpc_pilot.cli._confirm", return_value=False) as mock_confirm, \
             patch("hpc_pilot.dispatch.invoke") as mock_invoke:
            result = ansible_command(args)

        mock_confirm.assert_called_once()
        mock_invoke.assert_not_called()
        assert result == 0


# ---------------------------------------------------------------------------
# CLI: cron stub
# ---------------------------------------------------------------------------


class TestCronStub:
    def test_cron_command_registered(self):
        """hpc-pilot cron exits with 1 and a clear 'not implemented' message."""
        from hpc_pilot.cli import main

        result = main(["cron"])
        assert result == 1  # not yet implemented

    def test_cron_not_invalid_choice(self):
        """Running 'cron' does NOT produce an argparse 'invalid choice' error (exit 2)."""
        from hpc_pilot.cli import main

        result = main(["cron"])
        assert result != 2  # 2 = argparse parse error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
