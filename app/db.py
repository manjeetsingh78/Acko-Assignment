"""PostgreSQL access: engine, sessions, and creating the tables at startup.

D36: no migration tool. The schema is created from app/models.py with create_all,
which only creates missing tables. Restarting never drops or changes existing data.
"""
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from app.models import Base

log = logging.getLogger("pulse.db")


def utcnow() -> datetime:
    """Current time in UTC. All timestamps are stored as timestamptz."""
    return datetime.now(timezone.utc)


def make_engine(database_url: str) -> Engine:
    # pool_pre_ping: drop dead connections (e.g. after Postgres restarts) instead of failing a request.
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    # expire_on_commit=False: objects stay readable after commit (simpler to use in API code).
    return sessionmaker(bind=engine, expire_on_commit=False)


def wait_for_db(engine: Engine, attempts: int = 30, delay_s: float = 1.0) -> None:
    """Postgres may still be starting when Pulse starts. Retry before giving up."""
    for attempt in range(1, attempts + 1):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except Exception as exc:  # noqa: BLE001 - any connection error means "not ready yet"
            if attempt == attempts:
                raise
            log.warning("database not ready (attempt %d/%d): %s", attempt, attempts, exc.__class__.__name__)
            time.sleep(delay_s)


# D50: only ONE Pulse instance may run the scheduler + notifier, otherwise every service would be
# checked twice and incidents/webhooks raced. A Postgres session-level advisory lock is held for the
# lifetime of the process; it is released automatically if the process (or its connection) dies.
SCHEDULER_LOCK_KEY = 4_242_001


def try_acquire_scheduler_lock(engine: Engine):
    """Returns an open connection holding the lock, or None if another instance already holds it."""
    conn = engine.connect()
    # Detach from the pool: otherwise close() would only return the DB session to the pool, still
    # holding the lock. Detached, close() really ends the session, which releases the lock.
    conn.detach()
    acquired = conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": SCHEDULER_LOCK_KEY}).scalar()
    conn.commit()
    if acquired:
        return conn
    conn.close()
    return None


# create_all() never changes an EXISTING table, so columns added after the first release are
# added here. Each entry is (table, column, SQL). The SQL runs ONLY when the column is missing:
# an ALTER TABLE takes an AccessExclusive lock even when IF NOT EXISTS skips it, so probing first
# keeps every Pulse start lock-free when the schema is already current.
UPGRADE_STATEMENTS = [
    ("services", "flaky_opens_incident",
     "ALTER TABLE services ADD COLUMN IF NOT EXISTS flaky_opens_incident BOOLEAN NOT NULL DEFAULT FALSE"),
]


def _missing_columns(engine: Engine) -> list[str]:
    """The upgrade SQL that still needs to run, because its column is not there yet."""
    pairs = {(table, column) for table, column, _ in UPGRADE_STATEMENTS}
    found: set[tuple[str, str]] = set()
    with engine.connect() as conn:
        for table, column in pairs:
            exists = conn.execute(
                text("SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
                {"t": table, "c": column},
            ).first()
            if exists:
                found.add((table, column))
    return [sql for table, column, sql in UPGRADE_STATEMENTS if (table, column) not in found]


def init_db(engine: Engine) -> None:
    """Create any missing tables, then apply the small column upgrades. Safe to run on every start."""
    Base.metadata.create_all(engine)
    upgrades = _missing_columns(engine)
    if not upgrades:
        return
    with engine.begin() as conn:
        for statement in upgrades:
            conn.execute(text(statement))
