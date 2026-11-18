"""Integration: check results -> service state -> incidents -> outbox, against real Postgres."""
import pytest
from sqlalchemy import func, select

from app import incidents
from app.db import make_session_factory, utcnow
from app.models import HealthCheck, Incident, IncidentEvent, Notification, Service
from app.seed import seed_catalogue
from tests.integration.helpers import Clock, fail, feed, ok, service_id, slow


@pytest.fixture
def sessions(engine, test_settings):
    factory = make_session_factory(engine)
    with factory() as s:
        seed_catalogue(s, test_settings)
    return factory


@pytest.fixture
def pay(sessions):
    with sessions() as s:
        return service_id(s, "payments")


def count(sessions, model, *where):
    with sessions() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where))


def test_every_result_is_stored(sessions, pay):
    c = Clock()
    feed(sessions, pay, [ok(c), slow(c), fail(c)])
    assert count(sessions, HealthCheck, HealthCheck.service_id == pay) == 3


def test_full_outage_lifecycle(sessions, pay):
    c = Clock()
    states = feed(sessions, pay, [ok(c), fail(c), fail(c), fail(c), ok(c), ok(c), ok(c)])
    assert states == ["HEALTHY", "DEGRADED", "DOWN", "DOWN", "RECOVERING", "RECOVERING", "HEALTHY"]

    with sessions() as s:
        inc = s.scalar(select(Incident))
        assert inc.status == "RESOLVED" and inc.severity == "SEV1"   # payments is critical
        events = [e.event_type for e in s.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id)
                                                  .order_by(IncidentEvent.created_at, IncidentEvent.id))]
        assert events[:2] == ["detected", "opened"] and events.count("recovering") == 2 and "resolved" in events
        # MTTD input: first_failure_at is the FIRST failed check, not the one that crossed the threshold
        first_fail = s.scalars(select(HealthCheck).where(HealthCheck.success.is_(False))
                               .order_by(HealthCheck.started_at)).first()
        assert inc.first_failure_at == first_fail.started_at
        notes = sorted(n.event_type for n in s.scalars(select(Notification)))
        assert notes == ["incident.opened", "incident.resolved"]


def test_repeated_failures_never_create_duplicate_incidents_or_notifications(sessions, pay):
    c = Clock()
    feed(sessions, pay, [fail(c) for _ in range(10)])
    assert count(sessions, Incident) == 1
    assert count(sessions, Notification) == 1


def test_interrupted_recovery_keeps_the_same_incident(sessions, pay):
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c), ok(c), ok(c), fail(c), fail(c), ok(c), ok(c), ok(c)])
    assert count(sessions, Incident) == 1
    assert count(sessions, IncidentEvent, IncidentEvent.event_type == "recovery_interrupted") == 1
    assert count(sessions, Incident, Incident.status == "RESOLVED") == 1


def test_one_success_never_resolves(sessions, pay):
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c), ok(c)])
    assert count(sessions, Incident, Incident.status != "RESOLVED") == 1


def test_a_new_outage_after_recovery_is_a_new_incident(sessions, pay):
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c), ok(c), ok(c), ok(c), fail(c), fail(c)])
    with sessions() as s:
        rows = list(s.scalars(select(Incident).order_by(Incident.id)))
    assert [i.status for i in rows] == ["RESOLVED", "OPEN"]
    assert rows[0].trigger_key != rows[1].trigger_key


def test_recovery_resolves_only_service_down_incidents(sessions, pay):
    """D28: an incident with another trigger (e.g. a future manual/flaky one) is left alone."""
    with sessions() as s, s.begin():
        s.add(Incident(service_id=pay, trigger_type="manual", trigger_key="manual:1", title="Partner issue",
                       severity="SEV2", status="OPEN", public_message="m", first_failure_at=utcnow()))
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c)])
    assert count(sessions, Incident, Incident.status != "RESOLVED") == 2   # two unresolved coexist (R6)
    feed(sessions, pay, [ok(c), ok(c), ok(c)])
    with sessions() as s:
        assert s.scalar(select(Incident.status).where(Incident.trigger_type == "manual")) == "OPEN"
        assert s.scalar(select(Incident.status).where(Incident.trigger_type == "service_down")) == "RESOLVED"


def test_slow_service_is_degraded_without_incident(sessions, pay):
    c = Clock()
    assert feed(sessions, pay, [slow(c), slow(c), slow(c)]) == ["DEGRADED"] * 3
    assert count(sessions, Incident) == 0
    with sessions() as s:
        assert s.get(Service, pay).status_reason == "slow_response"


def test_flaky_service_is_degraded_with_reason_and_no_incident(sessions, pay):
    """payments: 3 failures in 60 s = flaky. Alternating never gives 2 consecutive failures."""
    c = Clock(step_s=10)
    states = feed(sessions, pay, [fail(c), ok(c), fail(c), ok(c), fail(c), ok(c)])
    assert "DOWN" not in states
    with sessions() as s:
        svc = s.get(Service, pay)
        assert (svc.current_status, svc.status_reason) == ("DEGRADED", "flaky")
    assert count(sessions, Incident) == 0


def test_flaky_window_expires(sessions, pay):
    c = Clock(step_s=10)
    feed(sessions, pay, [fail(c), ok(c), fail(c), ok(c), fail(c)])
    c.now = c.now.replace() + (c.step * 10)          # 100 s later: failures have left the 60 s window
    assert feed(sessions, pay, [ok(c)]) == ["HEALTHY"]


def test_outage_failures_do_not_make_a_recovered_service_look_flaky(sessions, pay):
    c = Clock(step_s=10)
    states = feed(sessions, pay, [fail(c), fail(c), fail(c), fail(c), ok(c), ok(c), ok(c)])
    assert states[-1] == "HEALTHY"


def test_counters_and_state_persist_in_the_database(sessions, pay):
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c), ok(c)])
    with sessions() as s:   # a brand-new session sees the same state, as after a restart
        svc = s.get(Service, pay)
        assert (svc.current_status, svc.consecutive_successes, svc.consecutive_failures) == ("RECOVERING", 1, 0)


def test_ack_and_notes(sessions, pay):
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c)])
    with sessions() as s, s.begin():
        inc_id = s.scalar(select(Incident.id))
        incidents.acknowledge(s, inc_id, "alice")
        incidents.acknowledge(s, inc_id, "bob")          # idempotent: first ack wins
        incidents.add_note(s, inc_id, "alice", "DB failover in progress")
    with sessions() as s:
        inc = s.get(Incident, inc_id)
        assert (inc.status, inc.acknowledged_by) == ("ACKNOWLEDGED", "alice")
    feed(sessions, pay, [ok(c), ok(c), ok(c)])
    with sessions() as s, s.begin():
        with pytest.raises(incidents.IncidentAlreadyResolved):
            incidents.acknowledge(s, inc_id, "alice")
    with sessions() as s, s.begin():
        with pytest.raises(incidents.IncidentAlreadyResolved):
            incidents.add_note(s, inc_id, "alice", "too late")


def test_webhook_payload_has_no_internal_error_text(sessions, pay):
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c), ok(c), ok(c), ok(c)])
    with sessions() as s:
        payloads = str([n.payload for n in s.scalars(select(Notification))])
    assert "10.12.4.7" not in payloads and "payments-db" not in payloads
    assert "HTTP 503" in payloads    # the error CATEGORY is there for engineers
