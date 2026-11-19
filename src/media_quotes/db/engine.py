"""Database engine and session factory (schema setup lives in bootstrap/Alembic)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url_value, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)
