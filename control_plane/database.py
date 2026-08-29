from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Base class for control-plane database models."""


def create_database(
    database_url: str,
) -> tuple[Engine, sessionmaker[Session]]:
    """Create the database engine and its session factory."""
    connect_args = (
        {"check_same_thread": False}
        if database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(database_url, connect_args=connect_args)
    session_factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return engine, session_factory


def session_scope(
    session_factory: sessionmaker[Session],
) -> Iterator[Session]:
    """Provide one database session for a request."""
    session = session_factory()
    try:
        yield session
    finally:
        session.close()

