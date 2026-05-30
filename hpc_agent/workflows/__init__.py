"""Management workflows - composite multi-tool procedures.

Each workflow function returns a Plan (spec 02 §3) that the executor will run.
"""

from hpc_agent.workflows.add_node import build as add_node
from hpc_agent.workflows.extend_allocation import build as extend_allocation
from hpc_agent.workflows.health_triage import build as health_triage
from hpc_agent.workflows.node_maintenance import build as node_maintenance
from hpc_agent.workflows.offboard_user import build as offboard_user
from hpc_agent.workflows.onboard_user import build as onboard_user
from hpc_agent.workflows.reconcile import build as reconcile
from hpc_agent.workflows.reporting import build as reporting
from hpc_agent.workflows.rolling_update import build as rolling_update

__all__ = [
    "add_node",
    "extend_allocation",
    "health_triage",
    "node_maintenance",
    "offboard_user",
    "onboard_user",
    "reconcile",
    "reporting",
    "rolling_update",
]
