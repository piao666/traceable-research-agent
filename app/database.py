"""SQLite database setup for Traceable Research Agent."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

WORKSPACE_DIR = Path("workspace")
DATABASE_PATH = WORKSPACE_DIR / "traceable_research_agent.sqlite"
DATABASE_URL = f"sqlite:///{DATABASE_PATH.as_posix()}"

WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, connection_record) -> None:
    """Make local SQLite more tolerant of concurrent smoke/API writers."""

    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


class Base(DeclarativeBase):
    """Base class for ORM models."""


def get_db() -> Generator[Session, None, None]:
    """Yield a database session for FastAPI dependencies."""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Upgrade the application database to the current Alembic revision."""

    from app.trace import models  # noqa: F401
    from app.evidence import models as evidence_models  # noqa: F401
    from app.memory import models as memory_models  # noqa: F401
    from scripts.migrate_database import migrate_database

    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    migrate_database()
