from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


@contextmanager
def session_scope() -> Generator[Session]:
    """One unit of work: commit on success, roll back on any error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Generator[Session]:
    """FastAPI dependency; commits after the handler returns, rolls back on error."""
    with session_scope() as session:
        yield session
