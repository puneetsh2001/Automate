from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # Hosted PostgreSQL (e.g. Neon) suspends idle databases and drops idle
        # connections: recycle them, keep the pool small, and fail fast on connect.
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
        if url.startswith("postgresql"):
            kwargs["connect_args"] = {"connect_timeout": 15}
    return create_engine(url, **kwargs)


engine = make_engine(get_settings().DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database(db_engine: Engine | None = None) -> bool:
    try:
        with (db_engine or engine).connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
