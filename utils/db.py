import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    # Default to a local SQLite file
    return "sqlite:///aicon.db"


engine = create_engine(_database_url(), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


@contextmanager
def db_session() -> Iterator["Session"]:
    from sqlalchemy.orm import Session  # local import for typing

    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from .models import Base  # noqa: WPS433

    Base.metadata.create_all(bind=engine)

    # Minimal, safe migrations for SQLite: add newly introduced columns if missing.
    # This avoids OperationalError when the ORM maps columns that the existing
    # SQLite file doesn't yet have. No effect on Postgres/MySQL.
    try:
        if engine.dialect.name == "sqlite":
            with engine.begin() as conn:
                def _cols(table: str) -> set[str]:
                    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
                    return {r[1] for r in rows}  # column name is index 1

                def _ensure(table: str, name: str, ddl: str) -> None:
                    existing = _cols(table)
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))

                # users table: add columns introduced after initial deploys
                u = _cols("users") if conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")) else set()
                if u:
                    _ensure("users", "user_uuid", "TEXT")
                    _ensure("users", "phone_number", "TEXT")
                    _ensure("users", "referrer_id", "INTEGER")
                    _ensure("users", "affiliate_rate_bps", "INTEGER")
                    _ensure("users", "affiliate_balance_cents", "INTEGER NOT NULL DEFAULT 0")
                    _ensure("users", "total_earned_cents", "INTEGER NOT NULL DEFAULT 0")
                    _ensure("users", "facility_code", "TEXT")
                    _ensure("users", "memory_enabled", "TEXT NOT NULL DEFAULT 'true'")
                    _ensure("users", "usage_paused", "TEXT NOT NULL DEFAULT 'false'")
                    _ensure("users", "usage_unlimited", "TEXT NOT NULL DEFAULT 'false'")

                # payouts table: ensure 'asset'
                p = _cols("payouts") if conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='payouts'")) else set()
                if p:
                    _ensure("payouts", "asset", "TEXT")
    except Exception:
        # Never block app startup due to best-effort migrations
        pass
