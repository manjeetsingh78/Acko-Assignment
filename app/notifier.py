"""Webhook sender (D29): delivers rows from the `notifications` outbox table.

The checker only WRITES outbox rows; this task does the network calls. A slow or broken
webhook receiver therefore never delays health checks or incident processing.
Pending rows survive a restart and are retried with bounded exponential backoff.
"""
import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import utcnow
from app.incidents import add_event
from app.models import Incident, Notification

log = logging.getLogger("pulse.notifier")

MAX_ATTEMPTS = 4
BACKOFF_SECONDS = [5, 15, 45]   # wait after attempt 1, 2, 3


def is_retryable(http_status: int | None) -> bool:
    """Network errors, 5xx and 429 may succeed later. Other 4xx mean our request is wrong."""
    return http_status is None or http_status >= 500 or http_status == 429


class Notifier:
    def __init__(self, sessions: sessionmaker, client: httpx.AsyncClient, webhook_url: str,
                 timeout_s: float = 5, poll_s: float = 2):
        self.sessions = sessions
        self.client = client
        self.webhook_url = webhook_url
        self.timeout_s = timeout_s
        self.poll_s = poll_s

    async def run_forever(self) -> None:
        while True:
            try:
                await self.deliver_due()
            except Exception:  # noqa: BLE001
                log.exception("notifier round failed")
            await asyncio.sleep(self.poll_s)

    def _due(self) -> list[tuple[int, dict]]:
        with self.sessions() as s:
            rows = s.execute(select(Notification.id, Notification.payload)
                             .where(Notification.delivery_status == "PENDING",
                                    Notification.next_attempt_at <= utcnow())
                             .order_by(Notification.id).limit(20))
            return [(r.id, r.payload) for r in rows]

    async def _post(self, payload: dict) -> tuple[int | None, str | None]:
        try:
            resp = await self.client.post(self.webhook_url, json=payload, timeout=self.timeout_s)
            return resp.status_code, None if 200 <= resp.status_code < 300 else resp.text[:300]
        except httpx.HTTPError as exc:
            return None, f"{type(exc).__name__}: {exc}"[:300]

    def _record(self, notification_id: int, http_status: int | None, error: str | None) -> str:
        with self.sessions() as s, s.begin():
            n = s.get(Notification, notification_id, with_for_update=True)
            incident = s.get(Incident, n.incident_id)
            now = utcnow()
            n.attempts += 1
            n.last_attempt_at = now
            n.last_http_status = http_status
            n.last_error = error
            result = f"HTTP {http_status}" if http_status else error
            if http_status is not None and 200 <= http_status < 300:
                n.delivery_status = "DELIVERED"
                add_event(s, incident, "notified",
                          f"Webhook {n.event_type} delivered ({result}, attempt {n.attempts})")
            elif is_retryable(http_status) and n.attempts < MAX_ATTEMPTS:
                n.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[n.attempts - 1])
            else:
                n.delivery_status = "FAILED"
                add_event(s, incident, "notify_failed",
                          f"Webhook {n.event_type} failed after {n.attempts} attempt(s): {result}")
            return n.delivery_status

    async def deliver_due(self) -> int:
        """Send every due notification once. Returns how many were attempted."""
        due = await asyncio.to_thread(self._due)
        for notification_id, payload in due:
            http_status, error = await self._post(payload)
            status = await asyncio.to_thread(self._record, notification_id, http_status, error)
            log.info("webhook %s -> %s (%s)", payload.get("event"), status, http_status or error)
        return len(due)
