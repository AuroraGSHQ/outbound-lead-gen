from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

# sqlite:///./data/leadgen.db -> make sure data/ exists before connecting.
if _settings.database_url.startswith("sqlite:///./"):
    Path(_settings.database_url.replace("sqlite:///./", "")).parent.mkdir(
        parents=True, exist_ok=True
    )

connect_args = {"check_same_thread": False} if _settings.database_url.startswith("sqlite") else {}
engine = create_engine(_settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    from app import models  # noqa: F401  (ensures models are registered)
    from app.models import Base

    Base.metadata.create_all(bind=engine)
    _apply_schema_sql()


def _apply_schema_sql() -> None:
    """Apply schema.sql (the JARVIS modules' tables: contacts, call_logs,
    deadlines, contracts, notifications) into the same database. Every
    statement in that file is idempotent (CREATE ... IF NOT EXISTS), so
    re-running this on every startup is safe.
    """
    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    if not schema_path.exists():
        return
    sql = schema_path.read_text()
    raw_conn = engine.raw_connection()
    try:
        if hasattr(raw_conn, "executescript"):
            raw_conn.executescript(sql)
        else:
            # Non-sqlite backend (e.g. Postgres): schema.sql is written
            # SQLite-flavored, so this needs adapting before it'll run here.
            cursor = raw_conn.cursor()
            for statement in filter(None, (s.strip() for s in sql.split(";"))):
                cursor.execute(statement)
            cursor.close()
        raw_conn.commit()
    finally:
        raw_conn.close()


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
