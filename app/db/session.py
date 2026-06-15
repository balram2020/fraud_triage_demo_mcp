from __future__ import annotations

import os
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://agent_user:agent_pass@localhost:5432/agent_service",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def postgres_connection_hint(error: Exception | None = None) -> str:
    """Actionable guidance when Postgres is unreachable."""
    detail = str(error).lower() if error else ""
    if "connection refused" in detail:
        return (
            "Postgres connection refused. Start Docker Desktop, run "
            "`docker compose up --build` in d36, and wait for postgres to become healthy."
        )
    if "could not translate host name" in detail or "name or service not known" in detail:
        return (
            "Postgres host not found. Inside Docker Compose use host `postgres`. "
            "On your laptop outside Docker use `localhost` in DATABASE_URL."
        )
    if "password authentication failed" in detail:
        return (
            "Postgres login failed. Check DATABASE_URL matches docker-compose credentials: "
            "agent_user / agent_pass / agent_service."
        )
    return (
        "Postgres is not ready yet. Wait ~30 seconds after `docker compose up`, then run "
        "`docker compose logs postgres` and look for 'database system is ready'."
    )


def get_postgres_status() -> tuple[bool, str]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "Postgres connection OK"
    except Exception as exc:
        return False, postgres_connection_hint(exc)


def check_postgres() -> bool:
    ok, _ = get_postgres_status()
    return ok


def init_db() -> None:
    """Create tables on startup (no Alembic migrations in this demo)."""
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        raise RuntimeError(
            "Could not initialize Postgres tables. "
            f"{postgres_connection_hint(exc)}"
        ) from exc


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
