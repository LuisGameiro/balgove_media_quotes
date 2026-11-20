"""Database engine and session factory (schema setup lives in bootstrap/Alembic)."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url_value, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(
    target: Engine | sessionmaker[Session] | Callable[[], Session],
) -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of database operations (Unit of Work)."""
    factory: Callable[[], Session] = (
        make_session_factory(target) if isinstance(target, Engine) else target
    )
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
