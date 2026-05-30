"""State store ORM models — the desired-state source of truth. See spec 00 §1.1.

Portable across PostgreSQL (production) and SQLite (tests). Live cluster state is
reconciled against these rows (spec 07 §6).
"""

from __future__ import annotations

import enum
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class NodeRole(str, enum.Enum):
    LOGIN = "login"
    COMPUTE_CPU = "compute_cpu"
    COMPUTE_GPU = "compute_gpu"
    CONTROLLER = "controller"


class NodeState(str, enum.Enum):
    UNKNOWN = "unknown"
    PROVISIONING = "provisioning"
    UP = "up"
    DRAINED = "drained"
    DOWN = "down"
    MAINT = "maint"


class ImageStatus(str, enum.Enum):
    BUILDING = "building"
    READY = "ready"
    FAILED = "failed"
    DEPRECATED = "deprecated"


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    hostname: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    mac: Mapped[str | None] = mapped_column(String(32))
    ip: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[NodeRole] = mapped_column(Enum(NodeRole))
    state: Mapped[NodeState] = mapped_column(Enum(NodeState), default=NodeState.UNKNOWN)
    image_id: Mapped[int | None] = mapped_column(ForeignKey("images.id"))
    profile: Mapped[str | None] = mapped_column(String(128))
    gpu_count: Mapped[int] = mapped_column(Integer, default=0)
    gpu_model: Mapped[str | None] = mapped_column(String(128))
    cpu_count: Mapped[int] = mapped_column(Integer, default=0)
    mem_mb: Mapped[int] = mapped_column(Integer, default=0)
    features: Mapped[str | None] = mapped_column(String(512))  # comma list, mirrors Slurm Features
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    image: Mapped["Image | None"] = relationship(back_populates="nodes")
    partitions: Mapped[list["PartitionMember"]] = relationship(
        back_populates="node", cascade="all, delete-orphan"
    )


class Image(Base):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    base_os: Mapped[str] = mapped_column(String(128))  # e.g. "rockylinux:9"
    kind: Mapped[NodeRole] = mapped_column(Enum(NodeRole))  # cpu vs gpu image
    spec_hash: Mapped[str] = mapped_column(String(64))  # idempotency
    cuda_version: Mapped[str | None] = mapped_column(String(32))
    driver_version: Mapped[str | None] = mapped_column(String(32))
    kernel_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[ImageStatus] = mapped_column(Enum(ImageStatus), default=ImageStatus.BUILDING)
    built_at: Mapped[datetime | None] = mapped_column(DateTime)

    nodes: Mapped[list["Node"]] = relationship(back_populates="image")


class Partition(Base):
    __tablename__ = "partitions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    max_time_min: Mapped[int | None] = mapped_column(Integer)
    default_qos: Mapped[str | None] = mapped_column(String(128))

    members: Mapped[list["PartitionMember"]] = relationship(
        back_populates="partition", cascade="all, delete-orphan"
    )


class PartitionMember(Base):
    __tablename__ = "partition_members"

    partition_id: Mapped[int] = mapped_column(ForeignKey("partitions.id"), primary_key=True)
    node_id: Mapped[int] = mapped_column(ForeignKey("nodes.id"), primary_key=True)

    partition: Mapped["Partition"] = relationship(back_populates="members")
    node: Mapped["Node"] = relationship(back_populates="partitions")


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    parent: Mapped[str | None] = mapped_column(String(128))
    organization: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(String(255))


class QOS(Base):
    __tablename__ = "qos"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    priority: Mapped[int | None] = mapped_column(Integer)
    max_wall_min: Mapped[int | None] = mapped_column(Integer)
    max_jobs_pu: Mapped[int | None] = mapped_column(Integer)
    max_tres: Mapped[str | None] = mapped_column(String(255))  # "cpu=128,gres/gpu=8"
    grp_tres: Mapped[str | None] = mapped_column(String(255))


class UserAssoc(Base):
    __tablename__ = "user_assocs"
    __table_args__ = (UniqueConstraint("user", "account", name="uq_user_account"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user: Mapped[str] = mapped_column(String(128), index=True)
    account: Mapped[str] = mapped_column(String(128))
    qos_list: Mapped[str] = mapped_column(String(512))  # comma list
    default_qos: Mapped[str | None] = mapped_column(String(128))
    fairshare: Mapped[int | None] = mapped_column(Integer)
