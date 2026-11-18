"""Integration: flaky incidents (D49), 7-day window (PO R9), single scheduler lock (D50), column upgrade."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update

from app.db import init_db, make_session_factory, try_acquire_scheduler_lock
from app.main import create_app
from app.models import Incident, Notification, Service
from app.seed import seed_catalogue
from tests.integration.helpers import Clock, fail, feed, ok, service_id


@pytest.fixture
def sessions(engine, test_settings):
    factory = make_session_factory(engine)
    with factory() as s:
        seed_catalogue(s, test_settings)
    return factory


def _set(sessions, name, **values):
    with sessions() as s, s.begin():
        if values:
            s.execute(update(Service).where(Service.name == name).values(**values))
        return service_id(s, name)


def incidents(sessions):
    with sessions() as s:
        return list(s.scalars(select(Incident).order_by(Incident.id)))


FLAKY = lambda c: [fail(c), ok(c), fail(c), ok(c), fail(c), ok(c)]   # 3 failures in 60 s, never 2 in a row


# ---------------- flaky incidents ----------------
def test_flaky_opens_no_incident_by_default(sessions):
    pay = _set(sessions, "payments")
    feed(sessions, pay, FLAKY(Clock(10)))
    assert incidents(sessions) == []


def test_flaky_incident_when_enabled_is_lower_severity_and_has_webhook(sessions):
    pay = _set(sessions, "payments", flaky_opens_incident=True)
    c = Clock(10)
    feed(sessions, pay, FLAKY(c) + [fail(c), ok(c)])          # stays flaky for a while: still ONE incident
    [inc] = incidents(sessions)
    assert (inc.trigger_type, inc.severity, inc.status) == ("flaky", "SEV2", "OPEN")   # payments critical -> SEV2
    with sessions() as s:
        assert s.scalar(select(Notification.payload).where(Notification.incident_id == inc.id))["status"] == "DEGRADED"


def test_flaky_incident_resolves_when_failures_leave_the_window(sessions):
    pay = _set(sessions, "payments", flaky_opens_incident=True)
    c = Clock(10)
    feed(sessions, pay, FLAKY(c))
    c.now += timedelta(seconds=120)                            # failures age out of the 60 s window
    feed(sessions, pay, [ok(c)])
    [inc] = incidents(sessions)
    assert inc.status == "RESOLVED"
    with sessions() as s:
        events = sorted(n.event_type for n in s.scalars(select(Notification)))
    assert events == ["incident.opened", "incident.resolved"]


def test_flaky_and_down_incidents_coexist_and_resolve_independently(sessions):
    """PO R6: two unresolved incidents for one service, each with its own trigger and lifecycle."""
    # 5-minute flaky window, so the flakiness outlasts the outage (with 60 s it would age out meanwhile)
    pay = _set(sessions, "payments", flaky_opens_incident=True, flaky_window_seconds=300)
    c = Clock(10)
    feed(sessions, pay, FLAKY(c) + [fail(c), fail(c)])         # flaky, then a real outage
    open_ = [(i.trigger_type, i.status) for i in incidents(sessions)]
    assert open_ == [("flaky", "OPEN"), ("service_down", "OPEN")]

    feed(sessions, pay, [ok(c), ok(c), ok(c)])                 # outage recovers: only the down incident resolves
    by_type = {i.trigger_type: i.status for i in incidents(sessions)}
    assert by_type == {"flaky": "OPEN", "service_down": "RESOLVED"}

    c.now += timedelta(seconds=400)
    feed(sessions, pay, [ok(c)])                               # flakiness clears later
    assert {i.trigger_type: i.status for i in incidents(sessions)} == {"flaky": "RESOLVED", "service_down": "RESOLVED"}


def test_flaky_incident_option_via_api(engine, test_settings):
    with TestClient(create_app(test_settings)) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "pulse-demo"})
        sid = next(s["id"] for s in client.get("/api/services").json() if s["name"] == "quotes")
        assert client.get(f"/api/services/{sid}").json()["flaky_opens_incident"] is False
        r = client.patch(f"/api/services/{sid}", json={"flaky_opens_incident": True})
        assert r.status_code == 200 and r.json()["flaky_opens_incident"] is True


# ---------------- 7-day window ----------------
def test_seven_day_window(engine, test_settings):
    app = create_app(test_settings)
    with TestClient(app) as client:
        client.post("/api/auth/login", json={"username": "operator", "password": "pulse-demo"})
        pay = next(s["id"] for s in client.get("/api/services").json() if s["name"] == "payments")
        c = Clock(10)
        c.now -= timedelta(days=3)                 # 3 days ago: outside 24 h, inside 7 days
        feed(app.state.sessions, pay, [fail(c), ok(c)])
        c.now += timedelta(days=3)
        feed(app.state.sessions, pay, [ok(c), ok(c)])

        day = client.get(f"/api/services/{pay}/metrics").json()
        week = client.get(f"/api/services/{pay}/metrics?window_hours=168").json()
        assert (day["window_hours"], day["availability"]["total_checks"], day["availability"]["percent"]) == (24, 2, 100.0)
        assert (week["window_hours"], week["availability"]["total_checks"], week["availability"]["percent"]) == (168, 4, 75.0)
        assert client.get(f"/api/services/{pay}/metrics?window_hours=0").status_code == 422
        row = next(r for r in client.get("/api/dashboard/summary").json()["services"] if r["service"]["id"] == pay)
        assert row["reliability_7d"]["window_hours"] == 168


# ---------------- single scheduler (advisory lock) ----------------
def test_only_one_instance_can_hold_the_scheduler_lock(engine):
    first = try_acquire_scheduler_lock(engine)
    assert first is not None
    assert try_acquire_scheduler_lock(engine) is None          # a second instance must stand by
    first.close()                                              # instance 1 stops / crashes
    second = try_acquire_scheduler_lock(engine)
    assert second is not None                                  # ...so another one can take over
    second.close()


def test_second_app_instance_runs_as_standby(engine, test_settings):
    from dataclasses import replace
    holder = try_acquire_scheduler_lock(engine)                # pretend another Pulse is running
    try:
        with TestClient(create_app(replace(test_settings, run_background=True))) as client:
            assert client.get("/healthz").json()["scheduler"] == "standby"
    finally:
        holder.close()


# ---------------- schema upgrade on an existing database ----------------
def test_init_db_adds_new_columns_to_an_existing_table(engine):
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE services DROP COLUMN flaky_opens_incident"))   # an "old" database
    init_db(engine)
    with engine.connect() as conn:
        cols = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='services'")).scalars().all()
    assert "flaky_opens_incident" in cols
