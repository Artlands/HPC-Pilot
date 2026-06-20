"""UNIX user management tools — useradd, userdel, usermod, SSH key deploy."""

from __future__ import annotations

import os
import shlex

from hpc_pilot.rbac import Role
from hpc_pilot.tools._registry import hpc_tool
from hpc_pilot.tools._run import _resolve_cluster, _run
from hpc_pilot.tools._validation import _USER_RE, _validate


def _shkey(username: str, key: str) -> str:
    """Shell-escape a key line for use in a grep/echo command."""
    return shlex.quote(key.strip())


@hpc_tool(
    name="hpc_system_user_add",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_user_add",
        "description": "Create a UNIX user account on the login node (useradd). Used for onboarding new HPC users.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string", "description": "Username for the new account"},
                "uid": {"type": "integer", "description": "Optional numeric UID"},
                "groups": {"type": "string", "description": "Comma-separated supplementary groups"},
                "shell": {"type": "string", "description": "Login shell (default /bin/bash)"},
                "home": {"type": "string", "description": "Home directory path"},
            },
            "required": ["username"],
        },
    },
)
def hpc_system_user_add(
    username: str,
    uid: int | None = None,
    groups: str = "",
    shell: str = "/bin/bash",
    home: str = "",
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Create a UNIX user account on the login node (useradd).

    Args:
        username: Username for the new account.
        uid: Optional numeric UID.
        groups: Optional comma-separated supplementary groups.
        shell: Login shell (default /bin/bash).
        home: Home directory path (default /home/<username>).
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = ["useradd"]
    if uid is not None:
        cmd += ["-u", str(uid)]
    if groups:
        cmd += ["-G", groups]
    if shell:
        cmd += ["-s", shell]
    if home:
        cmd += ["-d", home]
        _validate(home, "home directory")
    cmd.append(username)
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_system_user_delete",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_user_delete",
        "description": "Delete a UNIX user account (userdel).",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "remove_home": {"type": "boolean", "description": "Also remove home directory"},
            },
            "required": ["username"],
        },
    },
)
def hpc_system_user_delete(
    username: str,
    remove_home: bool = False,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Delete a UNIX user account (userdel).

    Args:
        username: Username to delete.
        remove_home: Also remove the user's home directory and mail spool.
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = ["userdel"]
    if remove_home:
        cmd.append("-r")
    cmd.append(username)
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_system_user_group_add",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_user_group_add",
        "description": "Add a user to a supplementary group (usermod -aG).",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "group": {"type": "string", "description": "Group name to add the user to"},
            },
            "required": ["username", "group"],
        },
    },
)
def hpc_system_user_group_add(
    username: str,
    group: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Add a user to a supplementary group (usermod -aG).

    Args:
        username: Username.
        group: Group name to add the user to.
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    _validate(group, "group name", _USER_RE)
    cl = _resolve_cluster(cluster)
    cmd = ["usermod", "-aG", group, username]
    return _run(cmd, cluster=cl, dry_run=dry_run)


@hpc_tool(
    name="hpc_system_ssh_key_deploy",
    role=Role.ADMIN,
    schema={
        "name": "hpc_system_ssh_key_deploy",
        "description": "Deploy an SSH public key to a user's authorized_keys on the login node.",
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "public_key": {"type": "string", "description": "SSH public key line to append"},
            },
            "required": ["username", "public_key"],
        },
    },
)
def hpc_system_ssh_key_deploy(
    username: str,
    public_key: str,
    *,
    cluster: str = "default",
    dry_run: bool = False,
) -> str:
    """Deploy an SSH public key for a user.

    Creates ``~<username>/.ssh/authorized_keys`` if absent and appends the
    public key. The ``.ssh`` directory and ``authorized_keys`` file are
    created with the correct permissions (700 / 600).

    Args:
        username: Username whose authorized_keys to modify.
        public_key: SSH public key line to append.
        dry_run: Preview without executing.
    """
    _validate(username, "username", _USER_RE)
    if not public_key.startswith("ssh-") and not public_key.startswith("ecdsa-"):
        raise ValueError(
            "public_key must start with 'ssh-' or 'ecdsa-' "
            "(e.g. 'ssh-ed25519 AAA...')"
        )
    cl = _resolve_cluster(cluster)
    home_dir = os.path.join("/home", username)

    # Build a chained command: create .ssh dir, set perms, append key
    cmds = " && ".join(
        [
            f"mkdir -p {home_dir}/.ssh",
            f"chmod 700 {home_dir}/.ssh",
            f"touch {home_dir}/.ssh/authorized_keys",
            f"chmod 600 {home_dir}/.ssh/authorized_keys",
            f"grep -qF {_shkey(username, public_key)} {home_dir}/.ssh/authorized_keys "
            f"|| echo {_shkey(username, public_key)} >> {home_dir}/.ssh/authorized_keys",
        ]
    )
    return _run(["sh", "-c", cmds], cluster=cl, dry_run=dry_run)
