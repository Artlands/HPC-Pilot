from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import hpc_agent.tools.ansible as ansible_mod
from hpc_agent.exec import audit
from hpc_agent.tools.ansible import ComposePlaybookIn, compose_playbook
from hpc_agent.tools.result import ToolStatus


@pytest.fixture(autouse=True)
def fresh_audit() -> None:
    audit.set_sink(audit.InMemorySink())


@pytest.fixture
def ansible_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(ansible_mod.settings, "ansible_dir", str(tmp_path))
    role_meta = tmp_path / "roles" / "common" / "meta"
    role_meta.mkdir(parents=True)
    role_meta.joinpath("argument_specs.yml").write_text(
        yaml.safe_dump(
            {
                "argument_specs": {
                    "main": {
                        "options": {
                            "timezone": {"type": "str"},
                            "packages": {"type": "list"},
                        }
                    }
                }
            }
        )
    )
    return tmp_path


def test_compose_playbook_dry_run_writes_nothing(ansible_dir: Path) -> None:
    res = compose_playbook(
        ComposePlaybookIn(
            name="site",
            target_group="compute_cpu",
            roles=["common"],
            vars={"timezone": "UTC"},
            dry_run=True,
        ),
        actor="alice",
    )

    assert res.status == ToolStatus.DRY_RUN
    assert res.diff is not None
    assert not (ansible_dir / "playbooks" / "site.yml").exists()


def test_compose_playbook_apply_writes_yaml(ansible_dir: Path) -> None:
    res = compose_playbook(
        ComposePlaybookIn(
            name="site",
            target_group="compute_cpu",
            roles=["common"],
            vars={"timezone": "UTC", "packages": ["vim"]},
            dry_run=False,
        ),
        actor="alice",
    )

    assert res.status == ToolStatus.OK
    path = ansible_dir / "playbooks" / "site.yml"
    assert path.exists()
    assert yaml.safe_load(path.read_text()) == [
        {
            "hosts": "compute_cpu",
            "become": True,
            "vars": {"timezone": "UTC", "packages": ["vim"]},
            "roles": ["common"],
        }
    ]


def test_compose_playbook_rejects_missing_role(ansible_dir: Path) -> None:
    res = compose_playbook(
        ComposePlaybookIn(
            name="site",
            target_group="compute_cpu",
            roles=["missing"],
            dry_run=False,
        ),
        actor="alice",
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"


def test_compose_playbook_rejects_unknown_var(ansible_dir: Path) -> None:
    res = compose_playbook(
        ComposePlaybookIn(
            name="site",
            target_group="compute_cpu",
            roles=["common"],
            vars={"unknown": True},
            dry_run=False,
        ),
        actor="alice",
    )

    assert res.status == ToolStatus.ERROR
    assert res.error and res.error.kind.value == "precondition"
    assert "unknown" in res.error.message


def test_compose_playbook_idempotent_noop(ansible_dir: Path) -> None:
    path = ansible_dir / "playbooks" / "site.yml"
    path.parent.mkdir(parents=True)
    inp = ComposePlaybookIn(
        name="site",
        target_group="compute_cpu",
        roles=["common"],
        vars={"timezone": "UTC"},
        dry_run=False,
    )
    path.write_text(ansible_mod._render_playbook(inp))

    res = compose_playbook(inp, actor="alice")

    assert res.status == ToolStatus.OK
    assert res.data and res.data.get("noop") is True
