"""Seeds the catalogue with the 4 starter-kit services, only if the catalogue is empty.

Running it again (or restarting) never duplicates or overwrites services an operator has edited.
"""
import sqlite3

from app.config import Settings
from app.db import utcnow

KIT_SERVICES = [
    ("quotes", "quotes-team"),
    ("payments", "payments-team"),
    ("claims", "claims-team"),
    ("notifications", "notifications-team"),
]


def seed_catalogue(conn: sqlite3.Connection, s: Settings) -> int:
    """Insert the kit services if no services exist. Returns how many were inserted."""
    if conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] > 0:
        return 0
    now = utcnow()
    conn.execute("BEGIN")
    for name, owner in KIT_SERVICES:
        conn.execute(
            """INSERT INTO services (name, health_url, interval_s, timeout_ms, owner, slo_target,
                                     created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, s.seed_url_template.format(name=name), s.default_interval_s,
             s.default_timeout_ms, owner, s.default_slo_target, now, now),
        )
    conn.execute("COMMIT")
    return len(KIT_SERVICES)
