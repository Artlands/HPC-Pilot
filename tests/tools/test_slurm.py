"""Tests for Phase 1 Slurm tools."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_cluster(slurm_bin: str = "/usr/bin"):
    cl = MagicMock()
    cl.slurm.side_effect = lambda bin_name: f"{slurm_bin}/{bin_name}"
    cl.ssh = None
    return cl


def _mock_run_ok(stdout: str = "OK"):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# hpc_slurm_job_status
# ---------------------------------------------------------------------------


class TestJobStatus:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_job_status

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "JobId=1234 JobName=test"
        result = hpc_slurm_job_status("1234")
        assert "JobId" in result

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_invalid_job_id(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_job_status

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="job_id"):
            hpc_slurm_job_status("not-a-number")

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_array_job_id(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_job_status

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "JobId=1234_1"
        result = hpc_slurm_job_status("1234_1")
        assert result


# ---------------------------------------------------------------------------
# hpc_slurm_job_hold / release / requeue
# ---------------------------------------------------------------------------


class TestJobControl:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_hold_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_job_hold

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_job_hold("999")
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert "hold" in cmd

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_hold_dry_run(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_job_hold

        cl = _mock_cluster()
        mock_cl.return_value = cl

        def _fake_run(cmd, **kw):
            return "DRY-RUN: " + " ".join(cmd) if kw.get("dry_run") else "ok"

        with patch("hpc_pilot.tools.slurm._run", wraps=_fake_run):
            result = hpc_slurm_job_hold("42", dry_run=True)
        assert result.startswith("DRY-RUN:")

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_hold_invalid_id(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_job_hold

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_job_hold("bad$id")

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_release_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_job_release

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_job_release("100")
        cmd = mock_run.call_args[0][0]
        assert "release" in cmd

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_requeue_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_job_requeue

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_job_requeue("200")
        cmd = mock_run.call_args[0][0]
        assert "requeue" in cmd

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_requeue_dry_run(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_job_requeue

        cl = _mock_cluster()
        mock_cl.return_value = cl
        _rv = "DRY-RUN: scontrol requeue 200"
        with patch("hpc_pilot.tools.slurm._run", return_value=_rv) as mr:
            hpc_slurm_job_requeue("200", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True


# ---------------------------------------------------------------------------
# hpc_slurm_job_cancel
# ---------------------------------------------------------------------------


class TestJobCancel:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_admin_can_cancel_any_job(self, mock_cl, mock_run):
        from hpc_pilot.rbac import Role
        from hpc_pilot.tools.slurm import hpc_slurm_job_cancel

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        # As admin, no ownership check
        hpc_slurm_job_cancel("5000", actor="alice", role=Role.ADMIN)
        # Should only call scancel (not scontrol show job)
        calls = mock_run.call_args_list
        assert len(calls) == 1
        cmd = calls[0][0][0]
        assert any("scancel" in c for c in cmd)

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_operator_cancels_own_job(self, mock_cl, mock_run):
        from hpc_pilot.rbac import Role
        from hpc_pilot.tools.slurm import hpc_slurm_job_cancel

        mock_cl.return_value = _mock_cluster()
        # First call: scontrol show job; second call: scancel
        mock_run.side_effect = [
            "JobId=5001 UserId=alice(1001) ...",
            "",
        ]
        hpc_slurm_job_cancel("5001", actor="alice", role=Role.OPERATOR)
        assert mock_run.call_count == 2

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_operator_cannot_cancel_others_job(self, mock_cl, mock_run):
        from hpc_pilot.rbac import Role
        from hpc_pilot.tools.slurm import hpc_slurm_job_cancel

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "JobId=5002 UserId=bob(1002) ..."
        with pytest.raises(PermissionError, match="bob"):
            hpc_slurm_job_cancel("5002", actor="alice", role=Role.OPERATOR)

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.rbac import Role
        from hpc_pilot.tools.slurm import hpc_slurm_job_cancel

        cl = _mock_cluster()
        mock_cl.return_value = cl
        with patch("hpc_pilot.tools.slurm._run") as mock_run:
            mock_run.return_value = "DRY-RUN: scancel 99"
            hpc_slurm_job_cancel("99", actor="root", role=Role.ADMIN, dry_run=True)
        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_invalid_job_id_rejected(self, mock_cl):
        from hpc_pilot.rbac import Role
        from hpc_pilot.tools.slurm import hpc_slurm_job_cancel

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_job_cancel("abc", actor="root", role=Role.ADMIN)


# ---------------------------------------------------------------------------
# hpc_slurm_reservation_*
# ---------------------------------------------------------------------------


class TestReservations:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_reservation_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "ReservationName=maint Nodes=node01"
        result = hpc_slurm_reservation_list()
        assert "ReservationName" in result

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_create_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_reservation_create

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "Reservation created"
        hpc_slurm_reservation_create(
            "maint", "node[01-04]", "now", "4:00:00", users="root"
        )
        cmd = mock_run.call_args[0][0]
        assert "reservationname=maint" in cmd
        assert "nodes=node[01-04]" in cmd
        assert "users=root" in cmd

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_create_dry_run(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_reservation_create

        cl = _mock_cluster()
        mock_cl.return_value = cl
        _rv = "DRY-RUN: scontrol create reservation maint"
        with patch("hpc_pilot.tools.slurm._run", return_value=_rv) as mr:
            hpc_slurm_reservation_create(
                "maint", "node01", "now", "1:00:00", dry_run=True
            )
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_create_invalid_name(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_reservation_create

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="reservation name"):
            hpc_slurm_reservation_create("bad name!", "node01", "now", "1h")

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_create_invalid_start(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_reservation_create

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="start time"):
            hpc_slurm_reservation_create("maint", "node01", "'; rm -rf /", "1h")

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_delete_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_reservation_delete

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "Reservation maint deleted"
        hpc_slurm_reservation_delete("maint")
        cmd = mock_run.call_args[0][0]
        assert "delete" in cmd
        assert "reservationname=maint" in cmd

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_delete_invalid_name(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_reservation_delete

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_reservation_delete("bad name!")


# ---------------------------------------------------------------------------
# hpc_slurm_partition_*
# ---------------------------------------------------------------------------


class TestPartitions:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_partition_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "PartitionName=gpu State=UP"
        result = hpc_slurm_partition_list()
        assert "PartitionName" in result

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_update_state_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_partition_update

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_partition_update("gpu", state="drain", dry_run=True)
        assert mock_run.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_update_invalid_state(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_partition_update

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="state"):
            hpc_slurm_partition_update("gpu", state="bogus")

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_update_invalid_name(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_partition_update

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_partition_update("bad name!", state="up")


# ---------------------------------------------------------------------------
# hpc_slurm_account_* / association_*
# ---------------------------------------------------------------------------


class TestAccounting:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_account_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_account_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "physics|Physics Dept|MIT|default\n"
        result = hpc_slurm_account_list()
        assert "physics" in result

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_account_create_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_account_create

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_account_create("physics", description="Physics Dept")
        cmd = mock_run.call_args[0][0]
        assert "add" in cmd and "account" in cmd and "physics" in cmd

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_account_create_dry_run(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_account_create

        cl = _mock_cluster()
        mock_cl.return_value = cl
        with patch("hpc_pilot.tools.slurm._run", return_value="DRY-RUN: ...") as mr:
            hpc_slurm_account_create("bio", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_account_create_invalid_name(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_account_create

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_account_create("bad name!")

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_association_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_association_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "physics|alice||normal|cpu=100\n"
        result = hpc_slurm_association_list(account="physics")
        assert "alice" in result

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_association_create_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_association_create

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_association_create("alice", "physics")
        cmd = mock_run.call_args[0][0]
        assert "add" in cmd and "user" in cmd
        assert any("account=physics" in c for c in cmd)

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_association_create_dry_run(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_association_create

        cl = _mock_cluster()
        mock_cl.return_value = cl
        with patch("hpc_pilot.tools.slurm._run", return_value="DRY-RUN: ...") as mr:
            hpc_slurm_association_create("bob", "bio", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_association_create_invalid_user(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_association_create

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_association_create("bad user!", "physics")


# ---------------------------------------------------------------------------
# hpc_slurm_qos_list / hpc_slurm_qos_create
# ---------------------------------------------------------------------------


class TestQOS:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_qos_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_qos_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "normal|7-00:00:00||cpu=1000|\n"
        result = hpc_slurm_qos_list()
        assert "normal" in result

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_qos_create_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_qos_create

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_qos_create("express", max_wall_min=60)
        cmd = mock_run.call_args[0][0]
        assert "add" in cmd and "qos" in cmd and "express" in cmd

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_qos_create_dry_run(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_qos_create

        cl = _mock_cluster()
        mock_cl.return_value = cl
        with patch("hpc_pilot.tools.slurm._run", return_value="DRY-RUN: ...") as mr:
            hpc_slurm_qos_create("express", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_qos_create_invalid_name(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_qos_create

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_qos_create("bad name!")


# ---------------------------------------------------------------------------
# hpc_slurm_fairshare / accounting / usage_report
# ---------------------------------------------------------------------------


class TestReports:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_fairshare(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_fairshare

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "Account|User|RawShares\nroot|root|1\n"
        hpc_slurm_fairshare()
        cmd = mock_run.call_args[0][0]
        assert "sshare" in cmd[0]
        assert "-Pl" in cmd

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_accounting_no_filters(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_accounting

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "JobID|JobName|User\n1|test|alice\n"
        hpc_slurm_accounting()
        cmd = mock_run.call_args[0][0]
        assert "sacct" in cmd[0]

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_accounting_with_filters(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_accounting

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "JobID|JobName|User\n1|test|alice\n"
        hpc_slurm_accounting(user="alice", start="2026-06-01", state="FAILED")
        cmd = mock_run.call_args[0][0]
        assert "--user" in cmd
        assert "--starttime" in cmd
        assert "--state" in cmd

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_accounting_invalid_user(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_accounting

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_slurm_accounting(user="bad user!")

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_accounting_invalid_start(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_accounting

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="start"):
            hpc_slurm_accounting(start="'; DROP TABLE")

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_usage_report_cluster(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_usage_report

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "Cluster|Login|Tres\ndefault|alice|cpu=100\n"
        hpc_slurm_usage_report("cluster")
        cmd = mock_run.call_args[0][0]
        assert "sreport" in cmd[0]

    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_usage_report_invalid_type(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_usage_report

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="report_type"):
            hpc_slurm_usage_report("unknown")


# ---------------------------------------------------------------------------
# hpc_slurm_sdiag
# ---------------------------------------------------------------------------


class TestSdiag:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_sdiag_returns_dict(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_sdiag

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = (
            "Server Thread count: 1\n"
            "Agent queue size: 0\n"
            "\nMain schedule statistics (microseconds):\n"
            "  Last cycle: 1500\n"
            "  Mean cycle: 2000\n"
            "\nBackfilling stats:\n"
            "  Total backfilled jobs: 5\n"
            "  Queue length: 12\n"
        )
        result = hpc_slurm_sdiag()
        assert isinstance(result, dict)
        assert "general" in result or len(result) > 0

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_sdiag_cmd(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_sdiag

        cl = _mock_cluster()
        mock_cl.return_value = cl
        mock_run.return_value = ""
        hpc_slurm_sdiag()
        cmd = mock_run.call_args[0][0]
        assert "sdiag" in cmd[0]


# ---------------------------------------------------------------------------
# hpc_slurm_config_show
# ---------------------------------------------------------------------------


class TestConfigShow:
    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_config_show(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_config_show

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "ClusterName=mycluster\nSlurmUser=slurm\n"
        result = hpc_slurm_config_show()
        assert "ClusterName" in result
        cmd = mock_run.call_args[0][0]
        assert "show" in cmd and "config" in cmd


# ---------------------------------------------------------------------------
# hpc_slurm_reconfigure
# ---------------------------------------------------------------------------


class TestReconfigure:
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_reconfigure_dry_run(self, mock_cl):
        from hpc_pilot.tools.slurm import hpc_slurm_reconfigure

        cl = _mock_cluster()
        mock_cl.return_value = cl
        _rv = "DRY-RUN: scontrol reconfigure"
        with patch("hpc_pilot.tools.slurm._run", return_value=_rv) as mr:
            hpc_slurm_reconfigure(dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.slurm._run")
    @patch("hpc_pilot.tools.slurm._resolve_cluster")
    def test_reconfigure_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.slurm import hpc_slurm_reconfigure

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_slurm_reconfigure()
        cmd = mock_run.call_args[0][0]
        assert "reconfigure" in cmd


# ---------------------------------------------------------------------------
# RBAC enforcement via dispatch.invoke
# ---------------------------------------------------------------------------


class TestRBACEnforcement:
    def test_job_hold_requires_operator(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with pytest.raises(PermissionError):
            invoke("hpc_slurm_job_hold", {"job_id": "1"}, role=Role.VIEWER, actor="test")

    def test_reservation_create_requires_admin(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with pytest.raises(PermissionError):
            invoke(
                "hpc_slurm_reservation_create",
                {"name": "m", "nodes": "n01", "start": "now", "duration": "1h"},
                role=Role.OPERATOR,
                actor="test",
            )

    def test_account_create_requires_superadmin(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with pytest.raises(PermissionError):
            invoke("hpc_slurm_account_create", {"name": "bio"}, role=Role.ADMIN, actor="test")

    def test_reconfigure_requires_superadmin(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with pytest.raises(PermissionError):
            invoke("hpc_slurm_reconfigure", {}, role=Role.ADMIN, actor="test")

    def test_sdiag_allowed_for_viewer(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with patch("hpc_pilot.tools.slurm._resolve_cluster") as mc, \
             patch("hpc_pilot.tools.slurm._run", return_value="Server Thread count: 1\n"):
            mc.return_value = _mock_cluster()
            result = invoke("hpc_slurm_sdiag", {}, role=Role.VIEWER, actor="test")
        assert result


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------


_SDIAG_SAMPLE = """\
Server Thread count: 3
Agent queue size: 0

Main schedule statistics (microseconds):
  Last cycle: 1500
  Mean cycle: 2000
  Cycles per minute: 30

Backfilling stats:
  Total backfilled jobs: 5
  Queue length: 12
  Last depth cycle: 120
"""

_SSHARE_SAMPLE = """\
             Account       User  RawShares  NormShares  RawUsage  NormUsage    FairShare
                root              1          1.000000    0         0.000000    1.000000
                root       root          1          1.000000    0         0.000000    1.000000
"""

_SACCT_SAMPLE = """\
JobID|JobName|User|Account|State|Elapsed|AllocTRES
1234|myjob|alice|physics|COMPLETED|01:23:45|cpu=4,mem=8G
1235|myjob2|bob|bio|FAILED|00:10:00|cpu=2
"""

_RESERVATIONS_SAMPLE = """\
ReservationName=maint StartTime=2026-07-01T09:00:00 EndTime=2026-07-01T13:00:00
   Nodes=node[01-04] NodeCnt=4 Users=root Flags=MAINT
"""


class TestParsers:
    def test_parse_sdiag(self):
        from hpc_pilot.tools.slurm_parsers import parse_sdiag

        result = parse_sdiag(_SDIAG_SAMPLE)
        assert isinstance(result, dict)

    def test_parse_sshare(self):
        from hpc_pilot.tools.slurm_parsers import parse_sshare

        rows = parse_sshare(_SSHARE_SAMPLE)
        assert isinstance(rows, list)

    def test_parse_sacct(self):
        from hpc_pilot.tools.slurm_parsers import parse_sacct

        rows = parse_sacct(_SACCT_SAMPLE)
        assert len(rows) == 2
        assert rows[0]["JobID"] == "1234"
        assert rows[0]["User"] == "alice"
        assert rows[1]["State"] == "FAILED"

    def test_parse_reservations(self):
        from hpc_pilot.tools.slurm_parsers import parse_reservations

        items = parse_reservations(_RESERVATIONS_SAMPLE)
        assert len(items) == 1
        assert items[0]["ReservationName"] == "maint"
        assert items[0].get("Nodes") == "node[01-04]"

    def test_parse_squeue_long(self):
        from hpc_pilot.tools.slurm_parsers import parse_squeue_long

        sample = """\
Wed Jun 19 12:00:00 2026
             JOBID PARTITION     NAME     USER    STATE       TIME TIME_LIMI  NODES NODELIST(REASON)
              1234       gpu    train    alice  RUNNING   00:10:00   2:00:00      1 gpu01
"""
        rows = parse_squeue_long(sample)
        assert len(rows) == 1
        assert rows[0]["JOBID"] == "1234"

    def test_parse_node_state_histogram(self):
        from hpc_pilot.tools.slurm import parse_node_state_histogram

        nodes = {
            "node01": {"NodeState": "IDLE"},
            "node02": {"NodeState": "ALLOC"},
            "node03": {"NodeState": "ALLOC"},
            "node04": {"NodeState": "DOWN*"},
            "node05": {"NodeState": "DRAIN"},
        }
        hist = parse_node_state_histogram(nodes)
        assert hist["IDLE"] == 1
        assert hist["ALLOC"] == 2
        assert hist["DOWN"] == 1
        assert hist["DRAIN"] == 1


# ---------------------------------------------------------------------------
# dispatch integration — all new tool names dispatch without error
# ---------------------------------------------------------------------------


_DISPATCH_VIEWER_TOOLS = [
    ("hpc_slurm_job_status", {"job_id": "1"}),
    ("hpc_slurm_reservation_list", {}),
    ("hpc_slurm_partition_list", {}),
    ("hpc_slurm_account_list", {}),
    ("hpc_slurm_association_list", {}),
    ("hpc_slurm_qos_list", {}),
    ("hpc_slurm_fairshare", {}),
    ("hpc_slurm_accounting", {}),
    ("hpc_slurm_usage_report", {}),
    ("hpc_slurm_config_show", {}),
]


class TestDispatchIntegration:
    @pytest.mark.parametrize("tool_name,args", _DISPATCH_VIEWER_TOOLS)
    def test_viewer_tools_dispatch(self, tmp_home, tool_name, args):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with patch("hpc_pilot.tools.slurm._resolve_cluster") as mc, \
             patch("hpc_pilot.tools.slurm._run", return_value="mocked output\n"):
            mc.return_value = _mock_cluster()
            result = invoke(tool_name, args, role=Role.VIEWER, actor="test")
        assert result

    def test_sdiag_dispatches(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with patch("hpc_pilot.tools.slurm._resolve_cluster") as mc, \
             patch("hpc_pilot.tools.slurm._run", return_value="Server Thread count: 1\n"):
            mc.return_value = _mock_cluster()
            result = invoke("hpc_slurm_sdiag", {}, role=Role.VIEWER, actor="test")
        assert result

    def test_job_hold_dispatches(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with patch("hpc_pilot.tools.slurm._resolve_cluster") as mc, \
             patch("hpc_pilot.tools.slurm._run", return_value="DRY-RUN: scontrol hold 1"):
            mc.return_value = _mock_cluster()
            result = invoke(
                "hpc_slurm_job_hold", {"job_id": "1", "dry_run": True},
                role=Role.OPERATOR, actor="test"
            )
        assert result

    def test_reservation_create_dispatches(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with patch("hpc_pilot.tools.slurm._resolve_cluster") as mc, \
             patch("hpc_pilot.tools.slurm._run", return_value="DRY-RUN: scontrol create ..."):
            mc.return_value = _mock_cluster()
            result = invoke(
                "hpc_slurm_reservation_create",
                {
                    "name": "maint", "nodes": "node01",
                    "start": "now", "duration": "1h", "dry_run": True,
                },
                role=Role.ADMIN, actor="test",
            )
        assert result

    def test_job_cancel_dispatches_with_ownership_check(self, tmp_home):
        from hpc_pilot.dispatch import invoke
        from hpc_pilot.rbac import Role

        with patch("hpc_pilot.tools.slurm._resolve_cluster") as mc, \
             patch("hpc_pilot.tools.slurm._run") as mr:
            mc.return_value = _mock_cluster()
            # First call: scontrol show job; second: scancel (dry_run path returns immediately)
            mr.side_effect = [
                "JobId=1 UserId=test(1000) ...",  # ownership check
                "DRY-RUN: scancel 1",
            ]
            result = invoke(
                "hpc_slurm_job_cancel",
                {"job_id": "1", "dry_run": True},
                role=Role.OPERATOR, actor="test",
            )
        assert result
