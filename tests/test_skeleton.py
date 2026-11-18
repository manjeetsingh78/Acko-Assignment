"""Skeleton tests: migrations, seeding, the one-open-incident constraint, and /healthz."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect, migrate, utcnow
from app.main import create_app
from app.seed import seed_catalogue


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "t.db"))
    migrate(c)
    yield c
    c.close()


def test_migrations_are_idempotent(conn):
    assert migrate(conn) == []  # second run applies nothing


def test_seed_inserts_four_kit_services_with_docker_names(conn):
    assert seed_catalogue(conn, Settings()) == 4
    rows = conn.execute("SELECT name, health_url, interval_s, timeout_ms FROM services ORDER BY id").fetchall()
    assert [r["name"] for r in rows] == ["quotes", "payments", "claims", "notifications"]
    assert rows[2]["health_url"] == "http://claims:8080/health"
    assert all(r["interval_s"] == 10 and r["timeout_ms"] == 5000 for r in rows)


def test_seed_does_not_duplicate(conn):
    seed_catalogue(conn, Settings())
    assert seed_catalogue(conn, Settings()) == 0
    assert conn.execute("SELECT COUNT(*) FROM services").fetchone()[0] == 4


def test_db_rejects_timeout_not_below_interval(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO services (name, health_url, interval_s, timeout_ms, owner, slo_target, created_at, updated_at)"
            " VALUES ('x', 'http://x', 5, 5000, 'o', 99.9, ?, ?)", (utcnow(), utcnow()))


def test_db_allows_only_one_unresolved_incident_per_service(conn):
    seed_catalogue(conn, Settings())
    ins = ("INSERT INTO incidents (code, service_id, status, first_failure_at, opened_at, public_message)"
           " VALUES (?, 1, ?, ?, ?, 'x')")
    conn.execute(ins, ("INC-001", "resolved", utcnow(), utcnow()))
    conn.execute(ins, ("INC-002", "open", utcnow(), utcnow()))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ins, ("INC-003", "acknowledged", utcnow(), utcnow()))


def test_healthz(tmp_path):
    app = create_app(Settings(db_path=str(tmp_path / "h.db")))
    with TestClient(app) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "services": 4}
