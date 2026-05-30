"""Repository pattern. See spec 00 §1.2.

Tool functions read/write state only through repositories — never raw SQL. Each repo
takes a Session (from db.session_scope) and exposes get/list/upsert/delete + domain
queries.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from hpc_agent.state.models import (
    QOS,
    Account,
    Image,
    Node,
    NodeRole,
    NodeState,
    Partition,
    PartitionMember,
    UserAssoc,
)


class NodeRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get(self, hostname: str) -> Node | None:
        return self.s.scalar(select(Node).where(Node.hostname == hostname))

    def all(self) -> list[Node]:
        return list(self.s.scalars(select(Node)))

    def by_role(self, role: NodeRole) -> list[Node]:
        return list(self.s.scalars(select(Node).where(Node.role == role)))

    def by_state(self, state: NodeState) -> list[Node]:
        return list(self.s.scalars(select(Node).where(Node.state == state)))

    def upsert(self, hostname: str, **fields: object) -> Node:
        node = self.get(hostname)
        if node is None:
            node = Node(hostname=hostname, **fields)
            self.s.add(node)
        else:
            for k, v in fields.items():
                setattr(node, k, v)
        self.s.flush()
        return node


class ImageRepo:
    def __init__(self, session: Session) -> None:
        self.s = session

    def get(self, name: str) -> Image | None:
        return self.s.scalar(select(Image).where(Image.name == name))

    def by_spec_hash(self, spec_hash: str) -> Image | None:
        return self.s.scalar(select(Image).where(Image.spec_hash == spec_hash))

    def all(self) -> list[Image]:
        return list(self.s.scalars(select(Image)))

    def upsert(self, name: str, **fields: object) -> Image:
        img = self.get(name)
        if img is None:
            img = Image(name=name, **fields)
            self.s.add(img)
        else:
            for k, v in fields.items():
                setattr(img, k, v)
        self.s.flush()
        return img


class SlurmRepo:
    """Accounts, QOS, user associations."""

    def __init__(self, session: Session) -> None:
        self.s = session

    # --- QOS ---
    def get_qos(self, name: str) -> QOS | None:
        return self.s.scalar(select(QOS).where(QOS.name == name))

    def list_qos(self) -> list[QOS]:
        return list(self.s.scalars(select(QOS)))

    def upsert_qos(self, name: str, **fields: object) -> QOS:
        qos = self.get_qos(name)
        if qos is None:
            qos = QOS(name=name, **fields)
            self.s.add(qos)
        else:
            for k, v in fields.items():
                if v is not None:
                    setattr(qos, k, v)
        self.s.flush()
        return qos

    # --- accounts ---
    def get_account(self, name: str) -> Account | None:
        return self.s.scalar(select(Account).where(Account.name == name))

    def upsert_account(self, name: str, **fields: object) -> Account:
        acct = self.get_account(name)
        if acct is None:
            acct = Account(name=name, **fields)
            self.s.add(acct)
        else:
            for k, v in fields.items():
                if v is not None:
                    setattr(acct, k, v)
        self.s.flush()
        return acct

    # --- user associations ---
    def get_assoc(self, user: str, account: str) -> UserAssoc | None:
        return self.s.scalar(
            select(UserAssoc).where(UserAssoc.user == user, UserAssoc.account == account)
        )

    def upsert_assoc(self, user: str, account: str, **fields: object) -> UserAssoc:
        assoc = self.get_assoc(user, account)
        if assoc is None:
            assoc = UserAssoc(user=user, account=account, **fields)
            self.s.add(assoc)
        else:
            for k, v in fields.items():
                if v is not None:
                    setattr(assoc, k, v)
        self.s.flush()
        return assoc

    # --- partitions ---
    def get_partition(self, name: str) -> Partition | None:
        return self.s.scalar(select(Partition).where(Partition.name == name))

    def upsert_partition(self, name: str, **fields: object) -> Partition:
        partition = self.get_partition(name)
        if partition is None:
            partition = Partition(name=name, **fields)
            self.s.add(partition)
        else:
            for k, v in fields.items():
                if v is not None:
                    setattr(partition, k, v)
        self.s.flush()
        return partition

    def add_partition_member(self, partition_name: str, node_hostname: str) -> None:
        """Add a node to a partition."""
        partition = self.get_partition(partition_name)
        if partition is None:
            raise ValueError(f"Partition {partition_name} not found")
        node = self.s.scalar(select(Node).where(Node.hostname == node_hostname))
        if node is None:
            raise ValueError(f"Node {node_hostname} not found")
        self.s.add(PartitionMember(partition_id=partition.id, node_id=node.id))
        self.s.flush()
