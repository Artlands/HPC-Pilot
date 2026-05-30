"""Approval gate backends. See spec 01 §3.

This module provides approval backend implementations:
- cli: y/N terminal prompt
- slack: message with Approve/Deny buttons
- api: pending approval record for async resolution

Each backend implements request_approval() which returns a Gate with
approved=True/False or requires_approval flag set.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hpc_agent.safety.diff import Diff
    from hpc_agent.safety.gate import Gate


class ApprovalBackend(ABC):
    """Abstract approval backend interface."""

    @abstractmethod
    def request_approval(self, gate: Gate, diff: Diff, actor: str) -> Gate:
        """Request approval for an action.

        Args:
            gate: The gate with approval requirements
            diff: The detailed diff of what will change
            actor: The user requesting approval

        Returns:
            Updated gate with approval status
        """


class CLIApproval(ApprovalBackend):
    """CLI approval backend - y/N terminal prompt."""

    def request_approval(self, gate: Gate, diff: Diff, actor: str) -> Gate:
        """Request approval via terminal prompt."""
        print("\n" + "=" * 70)
        print("APPROVAL REQUIRED")
        print("=" * 70)
        print(f"Actor: {actor}")
        print(f"Action: {diff.render()}")
        print(f"Blast radius: {diff.blast_radius} entities")
        print("=" * 70)
        print("\nDiff preview:")
        print(diff.render())
        print("=" * 70)

        # Ask for approval
        response = input(f"Approve action for {actor}? [y/N]: ").strip().lower()
        if response in ("y", "yes"):
            gate.approved = True
            gate.approver = actor
            gate.requires_approval = False
        else:
            gate.approved = False
            gate.denied = True
            gate.reason = "User declined approval"

        return gate


class MockApproval(ApprovalBackend):
    """Mock approval backend for testing."""

    def __init__(self, auto_approve: bool = True):
        self.auto_approve = auto_approve

    def request_approval(self, gate: Gate, diff: Diff, actor: str) -> Gate:
        """Mock approval - auto-approve or auto-deny."""
        if self.auto_approve:
            gate.approved = True
            gate.approver = "mock-approver"
            gate.requires_approval = False
        else:
            gate.approved = False
            gate.denied = True
            gate.reason = "Mock rejection"
        return gate


def request_approval(gate: Gate, diff: Diff, actor: str) -> Gate:
    """Request approval based on configured backend.

    Args:
        gate: The gate requiring approval
        diff: The diff showing what will change
        actor: The user requesting approval

    Returns:
        Updated gate with approval decision
    """
    backend_name = os.environ.get("APPROVAL_BACKEND", "cli").lower()

    backend: ApprovalBackend
    if backend_name == "cli":
        backend = CLIApproval()
    elif backend_name == "mock":
        backend = MockApproval(auto_approve=True)
    elif backend_name == "api":
        # API backend - create pending approval record
        # For now, fall back to CLI behavior
        backend = CLIApproval()
    else:
        # Unknown backend, fall back to CLI
        backend = CLIApproval()

    return backend.request_approval(gate, diff, actor)
