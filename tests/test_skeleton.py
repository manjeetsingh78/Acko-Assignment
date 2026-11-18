"""Skeleton tests: schema creation, seeding, key DB constraints, and /healthz."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import init_db, utcnow
from app.main import create_app
from app.models import Incident, Service
from app.seed import build_service, seed_catalogue


def test_init_db_is_idempotent(engine):
    init_db(engine)  # second run must not fail or drop anything


def test_seed_inserts_four_kit_services_with_docker_names_and_tiers(session, test_settings):
    assert seed_catalogue(session, test_settings) == 4
    rows = {s.name: s for s in session.scalars(select(Service))}
    assert set(rows) == {"quotes", "payments", "claims", "notifications"}
    assert rows["claims"].health_url == "http://claims:8080/health"
    # Interval comes from the criticality tier (D20), not from the name
    assert (rows["payments"].criticality, rows["payments"].check_interval_seconds) == ("critical", 10)
    assert (rows["notifications"].criticality, rows["notifications"].check_interval_seconds) == ("low", 30)
    # PO rules as defaults
    assert all(s.failure_threshold == 2 and s.recovery_threshold == 3 for s in rows.values())
    assert all(s.current_status == "UNKNOWN" for s in rows.values())  # D34: no data yet
    # D26: the flaky window can always hold enough checks to fire
    assert all(s.flaky_window_seconds >= s.flaky_failure_count * s.check_interval_seconds for s in rows.values())


def test_seed_does_not_duplicate(session, test_settings):
    seed_catalogue(session, test_settings)
    assert seed_catalogue(session, test_settings) == 0
    assert session.scalar(select(func.count()).select_from(Service)) == 4


@pytest.mark.parametrize("field, value", [
    ("timeout_ms", 10_000),        # timeout must be < interval (10 s)
    ("slow_threshold_ms", 5_000),  # slow threshold must be < timeout (5 s)
    ("criticality", "urgent"),     # unknown tier
])
def test_db_rejects_invalid_service_config(session, test_settings, field, value):
    svc = build_service("x", "o", "critical", "", test_settings)
    setattr(svc, field, value)
    session.add(svc)
    with pytest.raises(IntegrityError):
        session.commit()


def _incident(service_id: int, key: str, status: str = "OPEN") -> Incident:
    return Incident(service_id=service_id, trigger_type="service_down", trigger_key=key,
                    title="t", severity="SEV1", status=status, public_message="m",
                    first_failure_at=utcnow())


def test_db_allows_several_unresolved_incidents_per_service(session, test_settings):
    """PO answer (D27): multiple unresolved incidents for one service are allowed."""
    seed_catalogue(session, test_settings)
    sid = session.scalar(select(Service.id).where(Service.name == "payments"))
    session.add_all([_incident(sid, "down:1:1"), _incident(sid, "down:1:7", "ACKNOWLEDGED")])
    session.commit()
    assert session.scalar(select(func.count()).select_from(Incident).where(Incident.status != "RESOLVED")) == 2


def test_db_rejects_duplicate_incident_for_same_trigger(session, test_settings):
    """D27: the same trigger can never create two incidents."""
    seed_catalogue(session, test_settings)
    sid = session.scalar(select(Service.id).where(Service.name == "payments"))
    session.add(_incident(sid, "down:1:1"))
    session.commit()
    session.add(_incident(sid, "down:1:1"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_healthz(engine, test_settings):
    with TestClient(create_app(test_settings)) as client:
        r = client.get("/healthz")
    assert r.status_code == 200
    # scheduler "disabled": tests run without background tasks (else "active", or "standby" if another instance runs)
    assert r.json() == {"status": "ok", "db": "ok", "services": 4, "scheduler": "disabled"}
