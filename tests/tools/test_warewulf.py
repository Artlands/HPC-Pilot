"""Tests for Phase 2 Warewulf tools."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _mock_cluster():
    cl = MagicMock()
    cl.warewulf.side_effect = lambda name: f"/usr/bin/{name}"
    cl.ssh = None
    return cl


def _ww_run_ok(stdout: str = "OK"):
    return MagicMock(returncode=0, stdout=stdout, stderr="")


def _ww_run_fail(stderr: str = "error"):
    return MagicMock(returncode=1, stdout="", stderr=stderr)


# ===================================================================
# Image management: list / import / build / delete
# ===================================================================


class TestWarewulfImageList:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "IMAGE NAME         SIZE\nrocky9           1.2G\n"
        result = hpc_warewulf_image_list()
        assert "rocky9" in result
        assert "SIZE" in result


class TestWarewulfImageImport:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_import

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "importing rocky9..."
        result = hpc_warewulf_image_import("rocky9", "docker.rocky9")
        assert "importing" in result
        cmd = mock_run.call_args[0][0]
        assert "docker.rocky9" in cmd
        assert "rocky9" in cmd

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_import

        cl = _mock_cluster()
        mock_cl.return_value = cl
        with patch("hpc_pilot.tools.warewulf._run", return_value="DRY-RUN: wwctl image import") as mr:
            result = hpc_warewulf_image_import("rocky9", "docker.rocky9", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True
        assert "DRY-RUN" in result

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_invalid_source_rejected(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_import

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="image source"):
            hpc_warewulf_image_import("rocky9", "docker://rocky:9")

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_invalid_name_rejected(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_import

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError):
            hpc_warewulf_image_import("bad name!", "docker.rocky9")


class TestWarewulfImageBuild:
    @patch("hpc_pilot.tools.warewulf.os.path.isdir", return_value=False)
    @patch("hpc_pilot.tools.warewulf.os.path.exists", return_value=False)
    @patch("hpc_pilot.tools.warewulf.os.makedirs")
    @patch("hpc_pilot.tools.warewulf.subprocess.run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_spec_hash_determinism(
        self, mock_cl, mock_run, mock_mkdirs, mock_exists, mock_isdir
    ):
        """Same inputs produce the same spec_hash."""
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_build

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = _ww_run_ok("built")
        with patch("builtins.open"):
            r1 = hpc_warewulf_image_build("myimage", "rocky9", exec_steps=["step1", "step2"])
            r2 = hpc_warewulf_image_build("myimage", "rocky9", exec_steps=["step1", "step2"])
            assert r1["spec_hash"] == r2["spec_hash"]

    @patch("builtins.open")
    @patch("hpc_pilot.tools.warewulf.os.path.isdir", return_value=False)
    @patch("hpc_pilot.tools.warewulf.os.makedirs")
    @patch("hpc_pilot.tools.warewulf.subprocess.run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    @patch("hpc_pilot.tools.warewulf.json.load")
    @patch("hpc_pilot.tools.warewulf.os.path.exists", return_value=True)
    def test_cached_build_returns_cached(
        self, mock_exists, mock_json_load, mock_cl, mock_run, mock_mkdirs, mock_isdir, mock_open
    ):
        """When meta.json exists, return cached metadata without rebuilding."""
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_build

        mock_cl.return_value = _mock_cluster()
        mock_json_load.return_value = {
            "name": "myimage",
            "base": "rocky9",
            "spec_hash": "abcdef1234567890",
            "size_mb": 500,
            "cached": False,
        }
        result = hpc_warewulf_image_build("myimage", "rocky9")
        assert result["cached"] is True
        mock_run.assert_not_called()

    @patch("hpc_pilot.tools.warewulf.os.path.isdir", return_value=False)
    @patch("hpc_pilot.tools.warewulf.subprocess.run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_different_inputs_different_hash(self, mock_cl, mock_run, mock_isdir):
        """Different base images produce different spec_hashes."""
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_build

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = _ww_run_ok("built")
        with patch("hpc_pilot.tools.warewulf.os.makedirs"), \
             patch("hpc_pilot.tools.warewulf.os.path.exists", return_value=False), \
             patch("builtins.open"), \
             patch("hpc_pilot.tools.warewulf.json.load", side_effect=FileNotFoundError):
            r1 = hpc_warewulf_image_build("img", "rocky9")
            r2 = hpc_warewulf_image_build("img", "ubuntu")
            assert r1["spec_hash"] != r2["spec_hash"]

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_build

        mock_cl.return_value = _mock_cluster()
        result = hpc_warewulf_image_build("myimage", "rocky9", dry_run=True)
        assert result["dry_run"] is True
        assert "spec_hash" in result
        assert result["name"] == "myimage"


class TestWarewulfImageDelete:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_delete

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "image rocky9 deleted"
        result = hpc_warewulf_image_delete("rocky9")
        assert "deleted" in result
        cmd = mock_run.call_args[0][0]
        assert "delete" in cmd
        assert "rocky9" in cmd

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_image_delete

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf._run", return_value="DRY-RUN: ...") as mr:
            hpc_warewulf_image_delete("rocky9", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True


# ===================================================================
# Node lifecycle
# ===================================================================


class TestWarewulfNodeShow:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_show

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "nodename = node01\nipaddr = 10.0.0.1\n\nimage = rocky9\n"
        result = hpc_warewulf_node_show("node01")
        assert isinstance(result, list)
        assert len(result) >= 1
        first = result[0]
        assert first.get("nodename") == "node01"
        assert first.get("ipaddr") == "10.0.0.1"

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_invalid_name_rejected(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_show

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="node name"):
            hpc_warewulf_node_show("bad node!")


class TestWarewulfNodeAdd:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path_no_profile(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_add

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "node added"
        result = hpc_warewulf_node_add("node01", "0011.2233.4455", "10.0.0.1")
        assert "added" in result
        cmd = mock_run.call_args[0][0]
        assert "add" in cmd
        assert "--mac=0011.2233.4455" in cmd
        assert "--ipaddr=10.0.0.1" in cmd
        assert all("--profile" not in c for c in cmd)

    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path_with_profile(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_add

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "node added"
        hpc_warewulf_node_add("node01", "0011.2233.4455", "10.0.0.1", profile="default")
        cmd = mock_run.call_args[0][0]
        assert "--profile=default" in cmd

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_add

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf._run", return_value="DRY-RUN: ...") as mr:
            hpc_warewulf_node_add("node01", "0011.2233.4455", "10.0.0.1", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_invalid_mac_rejected(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_add

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="MAC"):
            hpc_warewulf_node_add("node01", "not a mac!", "10.0.0.1")


class TestWarewulfNodeSet:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_set

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "node updated"
        result = hpc_warewulf_node_set("node01", image="rocky9", ipaddr="10.0.0.2")
        assert "updated" in result
        cmd = mock_run.call_args[0][0]
        assert "set" in cmd
        assert "--image=rocky9" in cmd
        assert "--ipaddr=10.0.0.2" in cmd

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_set

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf._run", return_value="DRY-RUN: ...") as mr:
            hpc_warewulf_node_set("node01", dry_run=True, image="rocky9")
        assert mr.call_args.kwargs.get("dry_run") is True


class TestWarewulfNodeDelete:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_node_delete

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "node node01 deleted"
        result = hpc_warewulf_node_delete("node01")
        assert "deleted" in result
        cmd = mock_run.call_args[0][0]
        assert "delete" in cmd
        assert "node01" in cmd


# ===================================================================
# Power management
# ===================================================================


class TestWarewulfPower:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_status_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_power_status

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "node01: on"
        result = hpc_warewulf_power_status("node01")
        assert "on" in result
        cmd = mock_run.call_args[0][0]
        assert "power" in cmd and "status" in cmd

    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_power_on_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_power_on

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "powering on node01"
        result = hpc_warewulf_power_on("node01")
        assert "powering" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/bin/wwctl", "power", "on", "node01"]

    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_power_off_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_power_off

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "powering off node01"
        result = hpc_warewulf_power_off("node01")
        assert "powering" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/bin/wwctl", "power", "off", "node01"]

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_power_on_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_power_on

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf._run", return_value="DRY-RUN: ...") as mr:
            hpc_warewulf_power_on("node01", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_power_off_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_power_off

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf._run", return_value="DRY-RUN: ...") as mr:
            hpc_warewulf_power_off("node01", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_invalid_node_rejected(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_power_status

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="node name"):
            hpc_warewulf_power_status("bad node!")


# ===================================================================
# Profile management
# ===================================================================


class TestWarewulfProfile:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_profile_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_profile_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "PROFILE NAME\n  default\n  gpu\n"
        result = hpc_warewulf_profile_list()
        assert "default" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/bin/wwctl", "profile", "list"]

    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_profile_set_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_profile_set

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "profile updated"
        result = hpc_warewulf_profile_set("default", image="rocky9")
        assert "updated" in result
        cmd = mock_run.call_args[0][0]
        assert "set" in cmd
        assert "--image=rocky9" in cmd

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_profile_set_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_profile_set

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf._run", return_value="DRY-RUN: ...") as mr:
            hpc_warewulf_profile_set("default", dry_run=True, image="rocky9")
        assert mr.call_args.kwargs.get("dry_run") is True

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_profile_set_invalid_name(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_profile_set

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="profile name"):
            hpc_warewulf_profile_set("bad profile!", image="rocky9")


# ===================================================================
# Overlay management
# ===================================================================


class TestWarewulfOverlay:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_overlay_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "overlays: generic\n"
        result = hpc_warewulf_overlay_list()
        assert "generic" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/bin/wwctl", "overlay", "list"]

    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_overlay_build(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_build

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "building overlay generic..."
        result = hpc_warewulf_overlay_build("generic")
        assert "building" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/usr/bin/wwctl", "overlay", "build", "generic"]

    @patch("hpc_pilot.tools.warewulf.os.makedirs")
    @patch("hpc_pilot.tools.warewulf.subprocess.run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_overlay_edit_happy(self, mock_cl, mock_sp_run, mock_git_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_edit

        mock_cl.return_value = _mock_cluster()

        def git_side_effect(cmd_args, **kw):
            if "rev-parse" in cmd_args:
                return MagicMock(returncode=0, stdout="abc1234\n")
            return MagicMock(returncode=0)
        mock_sp_run.side_effect = git_side_effect
        # _run returns empty output for overlay build call
        mock_git_run.return_value = ""

        with patch("builtins.open"):
            result = hpc_warewulf_overlay_edit("generic", "myfile.cfg", "content here")
        assert result["overlay"] == "generic"
        assert result["commit"] == "abc1234"

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_overlay_edit_path_traversal_rejected(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_edit

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="Path traversal"):
            hpc_warewulf_overlay_edit("generic", "../../etc/shadow", "hack")

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_overlay_edit_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_edit

        mock_cl.return_value = _mock_cluster()
        result = hpc_warewulf_overlay_edit("generic", "myfile.cfg", "content", dry_run=True)
        assert result["dry_run"] is True
        assert result["overlay"] == "generic"

    @patch("hpc_pilot.tools.warewulf.os.path.exists", return_value=True)
    @patch("hpc_pilot.tools.warewulf.subprocess.run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_overlay_revert_happy(self, mock_cl, mock_run, mock_exists):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_revert

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = MagicMock(returncode=0)

        result = hpc_warewulf_overlay_revert("generic")
        assert result["overlay"] == "generic"
        assert result["commit"] == "HEAD"

    @patch("hpc_pilot.tools.warewulf.os.path.exists", return_value=False)
    def test_overlay_revert_no_git(self, mock_exists):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_revert

        with pytest.raises(RuntimeError, match="no git history"):
            hpc_warewulf_overlay_revert("generic")

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_overlay_revert_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_overlay_revert

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf.os.path.exists", return_value=True):
            result = hpc_warewulf_overlay_revert("generic", dry_run=True)
        assert result["dry_run"] is True
        assert result["commit"] == "HEAD"


# ===================================================================
# Configuration (DHCP, TFTP, NFS)
# ===================================================================


class TestWarewulfConfigure:
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    @patch("hpc_pilot.tools.warewulf._read_managed_conf")
    @patch("hpc_pilot.tools.warewulf._detect_external_edit")
    def test_configure_dhcp_changed(
        self, mock_ext, mock_read, mock_cl, mock_run
    ):
        from hpc_pilot.tools.warewulf import hpc_warewulf_configure_dhcp

        mock_cl.return_value = _mock_cluster()
        mock_ext.return_value = ""
        mock_read.return_value = {"dhcp": {"range_start": "10.0.0.100"}}

        # Use a side effect so binary reads return proper bytes for sha256
        _file_read_mock = MagicMock()
        _file_read_mock.read.return_value = b'{"dhcp":{"range_start":"10.0.0.200"}}'

        def _open_side(path, mode="r", **kw):
            if "b" in mode:
                m = MagicMock()
                m.__enter__.return_value = _file_read_mock
                return m
            m = MagicMock()
            m.__enter__.return_value = MagicMock()
            return m

        with patch("hpc_pilot.tools.warewulf.os.makedirs"), \
             patch("hpc_pilot.tools.warewulf.os.path.exists") as mock_pex, \
             patch("builtins.open", side_effect=_open_side), \
             patch("hpc_pilot.tools.warewulf.shutil.copy2"), \
             patch("hpc_pilot.tools.warewulf.os.rename"):
            mock_pex.return_value = True
            result = hpc_warewulf_configure_dhcp(range_start="10.0.0.200")
        assert isinstance(result, dict)
        assert "sha256" in result

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_configure_dhcp_no_change(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_configure_dhcp

        mock_cl.return_value = _mock_cluster()

        def _exists_side(path):
            return ".hpc-pilot" in path

        _file_read = MagicMock()
        _file_read.read.return_value = b'{"dhcp":{"range_start":"10.0.0.100"}}'

        def _open_side(path, mode="r", **kw):
            if "b" in mode:
                m = MagicMock()
                m.__enter__.return_value = _file_read
                return m
            m = MagicMock()
            m.__enter__.return_value = MagicMock()
            return m

        with patch("hpc_pilot.tools.warewulf._read_managed_conf") as mock_read, \
             patch("hpc_pilot.tools.warewulf._detect_external_edit", return_value=""), \
             patch("hpc_pilot.tools.warewulf.os.path.exists", side_effect=_exists_side), \
             patch("hpc_pilot.tools.warewulf.os.makedirs"), \
             patch("builtins.open", side_effect=_open_side), \
             patch("hpc_pilot.tools.warewulf.shutil.copy2"), \
             patch("hpc_pilot.tools.warewulf.os.rename"), \
             patch("hpc_pilot.tools.warewulf._run"):
            mock_read.return_value = {"dhcp": {"range_start": "10.0.0.100"}}
            result = hpc_warewulf_configure_dhcp(range_start="10.0.0.100")
        assert isinstance(result, dict)

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_configure_dhcp_dry_run(self, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_configure_dhcp

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.warewulf._read_managed_conf", return_value=None), \
             patch("hpc_pilot.tools.warewulf._detect_external_edit", return_value=""):
            result = hpc_warewulf_configure_dhcp(range_start="10.0.0.1", dry_run=True)
        assert result["dry_run"] is True

    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_configure_tftp_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_configure_tftp

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "TFTP configured"
        result = hpc_warewulf_configure_tftp()
        assert "TFTP" in result
        cmd = mock_run.call_args[0][0]
        assert "tftp" in cmd

    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_configure_nfs_happy(self, mock_cl, mock_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_configure_nfs

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "NFS configured"
        result = hpc_warewulf_configure_nfs()
        assert "NFS" in result
        cmd = mock_run.call_args[0][0]
        assert "nfs" in cmd

    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    @patch("hpc_pilot.tools.warewulf._read_managed_conf")
    def test_configure_dhcp_external_edit_detected(self, mock_read, mock_cl):
        from hpc_pilot.tools.warewulf import hpc_warewulf_configure_dhcp

        mock_cl.return_value = _mock_cluster()
        mock_read.return_value = {"dhcp": {"range_start": "10.0.0.100"}}

        _file_read_mock = MagicMock()
        _file_read_mock.read.return_value = b'{"dhcp":{"range_start":"10.0.0.200"}}'

        def _open_side(path, mode="r", **kw):
            if "b" in mode:
                m = MagicMock()
                m.__enter__.return_value = _file_read_mock
                return m
            m = MagicMock()
            m.__enter__.return_value = MagicMock()
            return m

        with patch("hpc_pilot.tools.warewulf._detect_external_edit", return_value="WARNING: external"), \
             patch("hpc_pilot.tools.warewulf.os.path.exists", return_value=True), \
             patch("builtins.open", side_effect=_open_side), \
             patch("hpc_pilot.tools.warewulf.os.makedirs"), \
             patch("hpc_pilot.tools.warewulf.shutil.copy2"), \
             patch("hpc_pilot.tools.warewulf.os.rename"), \
             patch("hpc_pilot.tools.warewulf._run"):
            result = hpc_warewulf_configure_dhcp(range_start="10.0.0.200")
        assert "external_edit_warning" in result


# ===================================================================
# Server status
# ===================================================================


class TestWarewulfServerStatus:
    @patch("hpc_pilot.tools.warewulf.subprocess.run")
    @patch("hpc_pilot.tools.warewulf._run")
    @patch("hpc_pilot.tools.warewulf._resolve_cluster")
    def test_server_status(self, mock_cl, mock_ww_run, mock_sp_run):
        from hpc_pilot.tools.warewulf import hpc_warewulf_server_status

        mock_cl.return_value = _mock_cluster()
        mock_ww_run.return_value = "wwctl server status: running"

        # systemctl responses
        def sp_side_effect(cmd, **kw):
            if "is-active" in cmd:
                return MagicMock(returncode=0, stdout="active\n", stderr="")
            if "is-enabled" in cmd:
                return MagicMock(returncode=0, stdout="enabled\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")
        mock_sp_run.side_effect = sp_side_effect

        result = hpc_warewulf_server_status()
        assert isinstance(result, dict)
        assert "wwctl_server_status" in result
        assert result["systemctl_active"] == "active"
        assert result["systemctl_enabled"] == "enabled"


# ===================================================================
# Parsers
# ===================================================================


class TestWarewulfParsers:
    def test_parse_warewulf_nodes(self):
        from hpc_pilot.tools.warewulf import parse_warewulf_nodes

        output = "NODE  IPADDR       MAC\nnode01    10.0.0.1  00:11:22:33:44:55\nnode02    10.0.0.2  00:11:22:33:44:66\n"
        result = parse_warewulf_nodes(output)
        assert len(result) == 2
        assert result[0]["NODE"] == "node01"
        assert result[1]["MAC"] == "00:11:22:33:44:66"
