import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
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

