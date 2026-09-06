import os
import logging
import asyncio

from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env", override=True)
load_dotenv(REPO_ROOT / ".env.example", override=False)

from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy import text

try:
    from .base import Base
except ImportError:
    from base import Base
 
logger = logging.getLogger(__name__)
 
# postgresql+asyncpg://user:password@host:port/dbname — note +asyncpg,
# the sync "postgresql://" driver won't work with this async engine.
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "4A@p705040742")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_DB = os.getenv("POSTGRES_DB", "postgres")

DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("DB_MAX_OVERFLOW", "20"))
DB_POOL_RECYCLE_SECONDS = int(os.getenv("DB_POOL_RECYCLE_SECONDS", "1800"))

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    db_path = REPO_ROOT / "agentic.db"
    DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://taupiusdloimmogvmupl.supabase.co")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "sb_publishable_zfgIswBdrh9YaSBzBzQvrw_zqyTfICn")

is_postgres = "postgresql" in DATABASE_URL
engine_kwargs = {
    "echo": os.environ.get("DB_ECHO_SQL", "false").lower() == "true",
}
if is_postgres:
    engine_kwargs.update({
        "pool_size": DB_POOL_SIZE,
        "max_overflow": DB_MAX_OVERFLOW,
        "pool_pre_ping": True,
        "pool_recycle": DB_POOL_RECYCLE_SECONDS,
    })

engine: AsyncEngine = create_async_engine(DATABASE_URL, **engine_kwargs)
 
 
async def wait_for_database(max_attempts: int = 30, delay_seconds: float = 2.0) -> None:
    """Call at service startup, before accepting traffic. In Docker
    Compose, Postgres often isn't ready the instant its container starts —
    this retries instead of crash-looping on a cold start."""
    for attempt in range(1, max_attempts + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database connection established (attempt %d)", attempt)
            return
        except Exception as exc:
            if attempt == max_attempts:
                logger.error("Database not reachable after %d attempts, giving up", max_attempts)
                raise
            logger.warning(
                "Database not ready (attempt %d/%d): %s — retrying in %.1fs. Set POSTGRES_HOST=... in .env if using remote Postgres.",
                attempt, max_attempts, exc, delay_seconds,
            )
            await asyncio.sleep(delay_seconds)
 
 
async def init_db_schema() -> None:
    """Creates all tables from Base.metadata (which pulls in every model
    registered under db/models/ via __init__.py). Fine for local dev and
    this project's current stage — switch to Alembic migrations once you
    have real data, since create_all() can't alter existing tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema created/verified via Base.metadata.create_all")
 
 
async def check_health() -> bool:
    """Lightweight liveness check — cheaper than wait_for_database's retry
    loop, meant for a /health endpoint called on every request rather than
    once at startup."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.exception("Database health check failed")
        return False
 
 
async def close_db() -> None:
    """Call on service shutdown so pooled connections are released
    cleanly rather than left dangling when the process exits."""
    await engine.dispose()


if __name__ == "__main__":
    import asyncio

    async def _main():
        print(f"DATABASE_URL={DATABASE_URL}")
        print("Waiting for database...")
        await wait_for_database()
        ok = await check_health()
        print(f"Health check: {'OK' if ok else 'FAILED'}")
        await close_db()

    asyncio.run(_main())