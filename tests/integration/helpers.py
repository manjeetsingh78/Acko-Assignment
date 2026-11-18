"""Helpers to fabricate probe results and feed them through the real check processor + DB."""
from datetime import timedelta

from sqlalchemy import select

from app.db import utcnow
from app.models import Service
from app.monitor.check_processor import process_check
from app.monitor.probe import ProbeResult

INTERNAL_ERROR = '{"status": "error", "error": "upstream payments-db timeout at 10.12.4.7:5432"}'


class Clock:
    """Fake, steadily advancing check times, so tests don't sleep."""

    def __init__(self, step_s: float = 10):
        self.now = utcnow() - timedelta(hours=1)
        self.step = timedelta(seconds=step_s)

    def tick(self):
        self.now += self.step
        return self.now


def ok(clock: Clock, ms: int = 20) -> ProbeResult:
    t = clock.tick()
    return ProbeResult(t, t + timedelta(milliseconds=ms), 200, ms, None, None)


def slow(clock: Clock, ms: int = 3000) -> ProbeResult:
    return ok(clock, ms)


def fail(clock: Clock) -> ProbeResult:
    t = clock.tick()
    return ProbeResult(t, t, 503, 15, "http", f"HTTP 503: {INTERNAL_ERROR}")


def feed(sessions, service_id: int, results: list[ProbeResult]) -> list[str]:
    return [process_check(sessions, service_id, r, r.started_at) for r in results]


def service_id(session, name: str) -> int:
    return session.scalar(select(Service.id).where(Service.name == name))
