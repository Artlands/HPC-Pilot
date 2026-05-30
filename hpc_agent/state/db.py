"""Engine + session management for the state store. See spec 00 §1.

Production uses the configured Postgres URL; tests pass an in-memory SQLite URL. Alembic
owns production migrations; `init_db` (create_all) is for tests/bootstrap only.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from hpc_agent.config.settings import settings
from hpc_agent.state.models import Base

_engine: Engine | None = None
_Session: sessionmaker[Session] | None = None


def configure(url: str | None = None, *, echo: bool = False) -> Engine:
    """(Re)configure the global engine. Returns it."""
    global _engine, _Session
    _engine = create_engine(url or settings.db_url, echo=echo, future=True)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def get_engine() -> Engine:
    if _engine is None:
        configure()
    assert _engine is not None
    return _engine


def init_db() -> None:
    """Create all tables. For tests/bootstrap; production uses Alembic."""
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session context. Commits on success, rolls back on error."""
    if _Session is None:
        configure()
    assert _Session is not None
    sess = _Session()
    try:
        yield sess
        sess.commit()
    except Exception:
        sess.rollback()
        raise
    finally:
        sess.close()
