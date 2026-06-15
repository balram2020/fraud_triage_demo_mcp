from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

from app.api import chat_router, health_router, sessions_router, traces_router
from app.db.session import get_postgres_status, init_db
from app.observability.logger import get_logger, log_event
from app.redis_controls import get_redis

load_dotenv()

logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_event(logger, "startup", app_env=os.getenv("APP_ENV", "local"))

    postgres_ok, postgres_msg = get_postgres_status()
    if postgres_ok:
        init_db()
        log_event(logger, "database_initialized", postgres=postgres_msg)
    else:
        log_event(logger, "postgres_unavailable", hint=postgres_msg)

    redis_ok, redis_msg = get_redis().status()
    if redis_ok:
        log_event(logger, "redis_ready", redis=redis_msg)
    else:
        log_event(logger, "redis_unavailable", hint=redis_msg)

    mock_llm = os.getenv("USE_MOCK_LLM", "").lower() in ("1", "true", "yes")
    if mock_llm:
        log_event(logger, "llm_mode", mode="mock")
    elif os.getenv("GROQ_API_KEY"):
        log_event(
            logger,
            "llm_mode",
            mode="groq",
            model=os.getenv("DEFAULT_MODEL", "llama-3.3-70b-versatile"),
        )
    else:
        log_event(
            logger,
            "llm_mode",
            mode="unconfigured",
            hint="Set GROQ_API_KEY in .env or USE_MOCK_LLM=true for offline runs",
        )

    yield
    log_event(logger, "shutdown")


app = FastAPI(
    title="Enterprise Agent Runtime",
    description=(
        "FastAPI makes the agent callable. "
        "Postgres and Redis make it operable."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(health_router)
app.include_router(chat_router)
app.include_router(sessions_router)
app.include_router(traces_router)
