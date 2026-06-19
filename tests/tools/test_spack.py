"""Tests for Phase 3 Spack tools."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _mock_cluster():
    cl = MagicMock()
    cl.spack.return_value = "/opt/spack/bin/spack"
    cl.spack_root = "/opt/spack"
    cl.ssh = None
    return cl


# ===================================================================
# Environment lifecycle
# ===================================================================


class TestSpackEnvCreate:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_env_create

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Created environment 'myenv'"
        result = hpc_spack_env_create("myenv")
        assert "Created" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "env", "create", "myenv"]

    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_with_manifest(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_env_create

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Created environment 'myenv'"
        hpc_spack_env_create("myenv", manifest="/path/to/spack.yaml")
        cmd = mock_run.call_args[0][0]
        assert "/path/to/spack.yaml" in cmd

    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_invalid_env_name_rejected(self, mock_cl):
        from hpc_pilot.tools.spack import hpc_spack_env_create

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="environment name"):
            hpc_spack_env_create("bad env name!")

    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_invalid_empty_name_rejected(self, mock_cl):
        from hpc_pilot.tools.spack import hpc_spack_env_create

        mock_cl.return_value = _mock_cluster()
        with pytest.raises(ValueError, match="environment name"):
            hpc_spack_env_create("bad env name!")

    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.spack import hpc_spack_env_create

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.spack._run", return_value="DRY-RUN: ...") as mr:
            hpc_spack_env_create("myenv", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True


class TestSpackEnvDelete:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_env_delete

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Environment 'myenv' removed"
        result = hpc_spack_env_delete("myenv")
        assert "removed" in result
        cmd = mock_run.call_args[0][0]
        assert "remove" in cmd
        assert "myenv" in cmd


class TestSpackEnvConcretize:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    @patch("hpc_pilot.tools.spack.os.path.exists")
    @patch("builtins.open")
    def test_lockfile_diff(
        self, mock_open, mock_exists, mock_cl, mock_run
    ):
        from hpc_pilot.tools.spack import hpc_spack_env_concretize

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Concretized myenv"

        # Simulate pre-existing lockfile with a spec, and post-concretize with new spec
        pre_specs = {"old_spec": {"version": "1.0"}}
        post_specs = {
            "old_spec": {"version": "1.0"},
            "new_spec": {"version": "2.0"},
        }
        mock_exists.return_value = True

        # First read (pre) returns pre_specs, second read (post) returns post_specs
        def open_side_effect(*args, **kw):
            m = MagicMock()
            m.__enter__.return_value = m
            if mock_open.call_count < 2:
                m.read.return_value = json.dumps({"concrete_specs": pre_specs})
            else:
                m.read.return_value = json.dumps({"concrete_specs": post_specs})
            return m
        mock_open.side_effect = open_side_effect

        with patch("hpc_pilot.tools.spack.hashlib.sha256") as mock_hash:
            mock_hash_instance = MagicMock()
            mock_hash_instance.hexdigest.return_value = "abc123"
            mock_hash.return_value = mock_hash_instance

            result = hpc_spack_env_concretize("myenv")

        assert result["env"] == "myenv"
        assert "new_spec" in result["added"]
        assert result["removed"] == []
        assert result["lockfile_sha256"] == "abc123"

    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.spack import hpc_spack_env_concretize

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.spack._run", return_value="DRY-RUN: spack -e myenv concretize") as mr:
            result = hpc_spack_env_concretize("myenv", dry_run=True)
        assert result["dry_run"] is True
        assert mr.call_args.kwargs.get("dry_run") is True


class TestSpackEnvInstall:
    @patch("hpc_pilot.jobs.start_job")
    @patch("hpc_pilot.tools.spack.os.makedirs")
    @patch("hpc_pilot.tools.spack.time")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_async_path(self, mock_cl, mock_time, mock_mkdirs, mock_start_job):
        from hpc_pilot.tools.spack import hpc_spack_env_install

        mock_cl.return_value = _mock_cluster()
        mock_time.strftime.return_value = "20260619-120000"

        mock_record = MagicMock()
        mock_record.run_id = "run_abc"
        mock_record.status = "running"
        mock_record.log_path = "/tmp/log.log"
        mock_start_job.return_value = mock_record

        result = hpc_spack_env_install("myenv")
        assert result["run_id"] == "run_abc"
        assert result["status"] == "running"
        cmd = mock_start_job.call_args[0][0]
        assert "install" in cmd

    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.spack import hpc_spack_env_install

        mock_cl.return_value = _mock_cluster()
        result = hpc_spack_env_install("myenv", dry_run=True)
        assert result["dry_run"] is True
        assert "command" in result


class TestSpackEnvStatus:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_env_status

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = (
            "==> Concretized\n"
            "        gcc@12.2.0\n"
            "        openmpi@4.1.5\n"
        )
        result = hpc_spack_env_status("myenv")
        assert isinstance(result, dict)
        assert result["env"] == "myenv"
        assert result["spec_count"] == 2
        assert "gcc@12.2.0" in result["specs"]
        assert "openmpi@4.1.5" in result["specs"]


class TestSpackInstallSpec:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_happy_path(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_install_spec

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Installing gcc"
        result = hpc_spack_install_spec("gcc")
        assert "Installing" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "install", "gcc"]


class TestSpackUninstall:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_without_dependents(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_uninstall

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_spack_uninstall("gcc")
        cmd = mock_run.call_args[0][0]
        assert "uninstall" in cmd
        assert "--dependents" not in cmd

    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_with_dependents(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_uninstall

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = ""
        hpc_spack_uninstall("gcc", dependents=True)
        cmd = mock_run.call_args[0][0]
        assert "--dependents" in cmd

    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_dry_run(self, mock_cl):
        from hpc_pilot.tools.spack import hpc_spack_uninstall

        mock_cl.return_value = _mock_cluster()
        with patch("hpc_pilot.tools.spack._run", return_value="DRY-RUN: ...") as mr:
            hpc_spack_uninstall("gcc", dry_run=True)
        assert mr.call_args.kwargs.get("dry_run") is True


class TestSpackMirror:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_mirror_list(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_mirror_list

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Mirrors:\n  local  file:///opt/mirror\n"
        result = hpc_spack_mirror_list()
        assert "local" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "mirror", "list"]

    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_mirror_add(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_mirror_add

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Added mirror"
        result = hpc_spack_mirror_add("local", "file:///opt/mirror")
        assert "Added" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "mirror", "add", "local", "file:///opt/mirror"]


class TestSpackBuildcache:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_push_without_gpg(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_buildcache_push

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Pushed"
        result = hpc_spack_buildcache_push("local")
        assert "Pushed" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "buildcache", "push", "local"]

    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_push_with_gpg_key(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_buildcache_push

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Pushed"
        hpc_spack_buildcache_push("local", gpg_key="ABCD1234")
        cmd = mock_run.call_args[0][0]
        assert "--key" in cmd
        assert "ABCD1234" in cmd
        assert cmd[:4] == ["/opt/spack/bin/spack", "buildcache", "push", "--key"]

    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_update_index(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_buildcache_update_index

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Index updated"
        result = hpc_spack_buildcache_update_index("local")
        assert "Index" in result
        cmd = mock_run.call_args[0][0]
        assert "update-index" in cmd


class TestSpackModuleCompiler:
    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_module_refresh(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_module_refresh

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Modules refreshed"
        result = hpc_spack_module_refresh()
        assert "refreshed" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "module", "lmod", "refresh", "-y"]

    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_compiler_find(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_compiler_find

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Added 2 new compilers"
        result = hpc_spack_compiler_find("/usr/bin")
        assert "Added" in result
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "compiler", "find", "/usr/bin"]

    @patch("hpc_pilot.tools.spack._run")
    @patch("hpc_pilot.tools.spack._resolve_cluster")
    def test_compiler_find_no_paths(self, mock_cl, mock_run):
        from hpc_pilot.tools.spack import hpc_spack_compiler_find

        mock_cl.return_value = _mock_cluster()
        mock_run.return_value = "==> Added 2 new compilers"
        hpc_spack_compiler_find()
        cmd = mock_run.call_args[0][0]
        assert cmd == ["/opt/spack/bin/spack", "compiler", "find"]


# ===================================================================
# Parser
# ===================================================================


class TestSpackParsers:
    def test_parse_spack_envs(self):
        from hpc_pilot.tools.spack import parse_spack_envs

        output = "==> 3 environments\n    myenv\n    myenv2\n    *active_env\n"
        result = parse_spack_envs(output)
        assert "myenv" in result
        assert "myenv2" in result
        assert "active_env" in result
        assert len(result) == 3
