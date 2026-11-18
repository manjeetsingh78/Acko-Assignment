"""Integration: webhook outbox delivery/retries, and scheduler isolation (no blocking, no overlap)."""
import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.db import make_session_factory, utcnow
from app.models import HealthCheck, IncidentEvent, Notification, Service
from app.monitor.probe import ProbeResult, probe
from app.monitor.scheduler import Scheduler
from app.notifier import MAX_ATTEMPTS, Notifier
from app.seed import seed_catalogue
from tests.integration.helpers import Clock, fail, feed, service_id


@pytest.fixture
def sessions(engine, test_settings):
    factory = make_session_factory(engine)
    with factory() as s:
        seed_catalogue(s, test_settings)
    return factory


def open_incident(sessions):
    with sessions() as s:
        pay = service_id(s, "payments")
    c = Clock()
    feed(sessions, pay, [fail(c), fail(c)])


def make_due_now(sessions):
    with sessions() as s, s.begin():
        s.execute(update(Notification).values(next_attempt_at=utcnow() - timedelta(seconds=1)))


async def run_rounds(sessions, responses, rounds):
    """Deliver with a fake receiver that answers with the given status codes in order."""
    received = []

    def handler(request: httpx.Request):
        received.append(request)
        code = responses.pop(0)
        return httpx.Response(code, json={"ok": code == 200})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        n = Notifier(sessions, client, "http://receiver/pulse")
        for _ in range(rounds):
            make_due_now(sessions)     # skip the real backoff wait
            await n.deliver_due()
    return received


def notification(sessions):
    with sessions() as s:
        return s.scalar(select(Notification))


def test_delivered_on_first_try(sessions):
    open_incident(sessions)
    received = asyncio.run(run_rounds(sessions, [200], rounds=1))
    n = notification(sessions)
    assert (n.delivery_status, n.attempts, n.last_http_status) == ("DELIVERED", 1, 200)
    assert received[0].url.path == "/pulse"
    with sessions() as s:
        assert s.scalar(select(IncidentEvent).where(IncidentEvent.event_type == "notified")) is not None


def test_retried_after_server_errors_then_delivered(sessions):
    open_incident(sessions)
    asyncio.run(run_rounds(sessions, [500, 503, 200], rounds=3))
    n = notification(sessions)
    assert (n.delivery_status, n.attempts) == ("DELIVERED", 3)


def test_backoff_delays_the_next_attempt(sessions):
    open_incident(sessions)
    asyncio.run(run_rounds(sessions, [500], rounds=1))
    n = notification(sessions)
    assert n.delivery_status == "PENDING" and n.next_attempt_at > utcnow()


def test_gives_up_after_max_attempts(sessions):
    open_incident(sessions)
    received = asyncio.run(run_rounds(sessions, [500] * 10, rounds=6))
    n = notification(sessions)
    assert (n.delivery_status, n.attempts) == ("FAILED", MAX_ATTEMPTS)
    assert len(received) == MAX_ATTEMPTS        # bounded: no more calls after giving up
    with sessions() as s:
        assert s.scalar(select(IncidentEvent).where(IncidentEvent.event_type == "notify_failed")) is not None


def test_client_error_is_not_retried(sessions):
    open_incident(sessions)
    asyncio.run(run_rounds(sessions, [400, 200], rounds=2))
    assert (notification(sessions).delivery_status, notification(sessions).attempts) == ("FAILED", 1)


def test_unreachable_receiver_is_retried(sessions):
    open_incident(sessions)

    def boom(request):
        raise httpx.ConnectError("connection refused")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(boom)) as client:
            await Notifier(sessions, client, "http://receiver/pulse").deliver_due()
    asyncio.run(go())
    n = notification(sessions)
    assert n.delivery_status == "PENDING" and "ConnectError" in n.last_error


# ---------------- probe ----------------
def test_probe_classifies_timeout_and_connection_errors():
    async def slow_handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    def refuse(request):
        raise httpx.ConnectError("refused")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow_handler)) as c:
            timed_out = await probe(c, "http://x/health", timeout_ms=200)
        async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as c:
            refused = await probe(c, "http://x/health", timeout_ms=200)
        return timed_out, refused

    timed_out, refused = asyncio.run(go())
    assert timed_out.error_kind == "timeout" and timed_out.response_time_ms < 600
    assert refused.error_kind == "connection"


# ---------------- scheduler ----------------
def test_slow_service_does_not_delay_others_and_never_overlaps_itself(sessions):
    """payments answers after 1.5 s; the other three must still be checked on time (low lag)."""
    with sessions() as s, s.begin():
        s.execute(update(Service).values(check_interval_seconds=2, timeout_ms=1800, slow_threshold_ms=1000,
                                         flaky_window_seconds=60))
        slow_id = service_id(s, "payments")

    in_flight: dict[int, int] = {}
    max_in_flight: dict[int, int] = {}

    async def fake_probe(client, url, timeout_ms):
        sid = slow_id if "payments" in url else hash(url)
        in_flight[sid] = in_flight.get(sid, 0) + 1
        max_in_flight[sid] = max(max_in_flight.get(sid, 0), in_flight[sid])
        start = utcnow()
        await asyncio.sleep(1.5 if "payments" in url else 0.01)
        in_flight[sid] -= 1
        return ProbeResult(start, utcnow(), 200, 1500 if "payments" in url else 10, None, None)

    async def go():
        sched = Scheduler(sessions, client=None, probe_fn=fake_probe)
        await sched.sync_tasks()
        await asyncio.sleep(5)
        await sched.stop()

    asyncio.run(go())

    assert max(max_in_flight.values()) == 1        # no service ever had two checks in flight
    with sessions() as s:
        rows = s.execute(select(HealthCheck.service_id, HealthCheck.scheduled_at, HealthCheck.started_at)).all()
    others = [r for r in rows if r.service_id != slow_id]
    assert len(others) >= 6                          # 3 fast services x at least 2 checks in 5 s
    worst_lag = max((r.started_at - r.scheduled_at).total_seconds() for r in others)
    assert worst_lag < 0.5
    with sessions() as s:
        assert s.get(Service, slow_id).current_status == "DEGRADED"   # 1.5 s > 1 s slow threshold
