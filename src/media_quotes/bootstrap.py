"""CLI: python -m media_quotes.bootstrap

1. Applies the schema with Alembic (`alembic upgrade head`, DEC-15).
2. Syncs the authoritative source registry from data/sources.csv.

No network access, no source contact. Raw DDL is never executed here.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from .config import PROJECT_ROOT, get_settings
from .db.engine import create_db_engine, make_session_factory
from .db.repository import sync_sources
from .observability import configure_logging
from .registry import load_sources_csv

_ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def resolve_sources_csv(configured: Path) -> Path:
    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured


def initialize_database(engine: Engine, alembic_ini: Path | None = None) -> int:
    """Apply Alembic migrations through a caller-owned connection."""
    config = Config(str(alembic_ini or _ALEMBIC_INI))
    with engine.connect() as connection:
        config.attributes = {"connection": connection}
        command.upgrade(config, "head")
        # One revision is applied by `head` on a fresh database.
        return 1


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings)

    statements = initialize_database(engine, settings.alembic_ini)
    rows = load_sources_csv(resolve_sources_csv(settings.sources_csv))

    with make_session_factory(engine)() as session:
        synced = sync_sources(session, rows)

    print(f"Bootstrap complete: {statements} SQL statements, {synced} sources synced")


if __name__ == "__main__":
    main()
