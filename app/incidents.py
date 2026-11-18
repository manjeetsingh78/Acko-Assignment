"""Incident manager: open, resolve, acknowledge, add notes, and record the timeline.

Every function takes an open SQLAlchemy session and runs inside the caller's transaction,
so an incident, its timeline events and its webhook outbox row are saved together or not at all.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import FLAKY_SEVERITY, TIER_SEVERITY
from app.db import utcnow
from app.domain import messages
from app.models import HealthCheck, Incident, IncidentEvent, IncidentNote, Notification, Service

TRIGGER_SERVICE_DOWN = "service_down"
TRIGGER_FLAKY = "flaky"


class IncidentNotFound(Exception):
    pass


class IncidentAlreadyResolved(Exception):
    pass


def add_event(s: Session, incident: Incident, event_type: str, description: str, *,
              is_public: bool = False, details: dict | None = None, at: datetime | None = None) -> None:
    """is_public=True only for customer-safe template text (it is shown on the status page)."""
    s.add(IncidentEvent(incident_id=incident.id, event_type=event_type, description=description,
                        is_public=is_public, details=details, created_at=at or utcnow()))


def enqueue_notification(s: Session, incident: Incident, service: Service, event: str, status: str,
                         message: str) -> None:
    """Write a webhook to the outbox (D29). The notifier task sends it later.

    UNIQUE(incident_id, event_type) means each incident sends each event at most once.
    """
    already = s.scalar(select(Notification.id).where(Notification.incident_id == incident.id,
                                                     Notification.event_type == event))
    if already:
        return
    # next_attempt_at uses the app clock, the same clock the notifier compares against.
    n = Notification(incident_id=incident.id, event_type=event, payload={}, delivery_status="PENDING",
                     next_attempt_at=utcnow(), created_at=utcnow())
    s.add(n)
    s.flush()  # gives n.id, used as the event_id consumers can deduplicate on
    n.payload = messages.webhook_payload(
        event, notification_id=n.id, incident_id=incident.id, service=service.name,
        severity=incident.severity, status=status, message=message,
        started_at=incident.first_failure_at, timestamp=utcnow(), resolved_at=incident.resolved_at)


def _check_details(check: HealthCheck) -> dict:
    """Technical detail for the operator timeline (never shown publicly)."""
    return {"check_id": check.id, "outcome": check.outcome, "http_status": check.http_status,
            "response_time_ms": check.response_time_ms, "error": check.error_message}


def open_down_incident(s: Session, service: Service, first_failed: HealthCheck, current: HealthCheck,
                       failure_summary: str) -> Incident:
    """Open an incident when a service enters DOWN. Idempotent thanks to the unique trigger_key (D27)."""
    trigger_key = f"down:{service.id}:{first_failed.id}"
    existing = s.scalar(select(Incident).where(Incident.trigger_key == trigger_key))
    if existing:
        return existing

    incident = Incident(
        service_id=service.id, trigger_type=TRIGGER_SERVICE_DOWN, trigger_key=trigger_key,
        title=f"{service.name} is down ({failure_summary})", severity=TIER_SEVERITY[service.criticality],
        status="OPEN", public_message=messages.public_incident_message(service.name),
        first_failure_at=first_failed.started_at, opened_at=utcnow())
    s.add(incident)
    s.flush()

    add_event(s, incident, "detected", f"First failed health check: {failure_summary}",
              details=_check_details(first_failed), at=first_failed.started_at)
    add_event(s, incident, "opened",
              f"Incident opened after {service.failure_threshold} consecutive failed checks",
              details=_check_details(current))
    add_event(s, incident, "public_update", messages.public_update("opened", service.name), is_public=True)
    enqueue_notification(s, incident, service, "incident.opened", "DOWN",
                         f"{service.name} failed {service.failure_threshold} consecutive health checks "
                         f"({failure_summary})")
    return incident


def unresolved_down_incidents(s: Session, service_id: int) -> list[Incident]:
    return list(s.scalars(
        select(Incident)
        .where(Incident.service_id == service_id, Incident.trigger_type == TRIGGER_SERVICE_DOWN,
               Incident.status != "RESOLVED")
        .order_by(Incident.id)
        .with_for_update()))


def record_recovery_progress(s: Session, service: Service, check: HealthCheck, successes: int) -> None:
    for inc in unresolved_down_incidents(s, service.id):
        add_event(s, inc, "recovering",
                  f"Recovery check {successes}/{service.recovery_threshold} passed",
                  details=_check_details(check))
        if successes == 1:  # tell customers once, not on every check
            add_event(s, inc, "public_update", messages.public_update("recovering", service.name),
                      is_public=True)


def record_recovery_interrupted(s: Session, service: Service, check: HealthCheck, failure_summary: str) -> None:
    for inc in unresolved_down_incidents(s, service.id):
        add_event(s, inc, "recovery_interrupted",
                  f"Recovery interrupted by a failed check ({failure_summary}); success counter reset",
                  details=_check_details(check))


def resolve_after_recovery(s: Session, service: Service, check: HealthCheck, final_status: str) -> list[Incident]:
    """D28: confirmed recovery resolves only this service's *service_down* incidents."""
    resolved = []
    for inc in unresolved_down_incidents(s, service.id):
        inc.status = "RESOLVED"
        inc.resolved_at = utcnow()
        add_event(s, inc, "resolved",
                  f"Resolved automatically after {service.recovery_threshold} consecutive successful checks",
                  details=_check_details(check))
        add_event(s, inc, "public_update", messages.public_update("resolved", service.name), is_public=True)
        enqueue_notification(s, inc, service, "incident.resolved", final_status,
                             f"{service.name} recovered after {service.recovery_threshold} consecutive "
                             "successful health checks")
        resolved.append(inc)
    return resolved


# ---------------- flaky incidents (D49: only when the service has flaky_opens_incident on) ----------------
def unresolved_flaky_incidents(s: Session, service_id: int) -> list[Incident]:
    return list(s.scalars(
        select(Incident)
        .where(Incident.service_id == service_id, Incident.trigger_type == TRIGGER_FLAKY,
               Incident.status != "RESOLVED")
        .order_by(Incident.id)
        .with_for_update()))


def open_flaky_incident(s: Session, service: Service, check: HealthCheck, failures_in_window: int) -> Incident:
    """A second, independent trigger: it can be open at the same time as a service_down incident (PO R6)."""
    trigger_key = f"flaky:{service.id}:{check.id}"
    existing = s.scalar(select(Incident).where(Incident.trigger_key == trigger_key))
    if existing:
        return existing
    summary = f"{failures_in_window} failed checks within {service.flaky_window_seconds} s"
    incident = Incident(
        service_id=service.id, trigger_type=TRIGGER_FLAKY, trigger_key=trigger_key,
        title=f"{service.name} is flaky ({summary})", severity=FLAKY_SEVERITY[service.criticality],
        status="OPEN", public_message=messages.public_flaky_incident_message(service.name),
        first_failure_at=check.started_at, opened_at=utcnow())
    s.add(incident)
    s.flush()
    add_event(s, incident, "opened",
              f"Flaky threshold crossed: {summary} (threshold {service.flaky_failure_count})",
              details=_check_details(check))
    add_event(s, incident, "public_update", messages.public_update("flaky_opened", service.name), is_public=True)
    enqueue_notification(s, incident, service, "incident.opened", "DEGRADED",
                         f"{service.name} is failing intermittently: {summary}")
    return incident


def resolve_flaky_incidents(s: Session, service: Service, check: HealthCheck) -> list[Incident]:
    resolved = []
    for inc in unresolved_flaky_incidents(s, service.id):
        inc.status = "RESOLVED"
        inc.resolved_at = utcnow()
        add_event(s, inc, "resolved",
                  f"Resolved automatically: fewer than {service.flaky_failure_count} failed checks "
                  f"in the last {service.flaky_window_seconds} s", details=_check_details(check))
        add_event(s, inc, "public_update", messages.public_update("flaky_resolved", service.name), is_public=True)
        enqueue_notification(s, inc, service, "incident.resolved", service.current_status,
                             f"{service.name} is no longer failing intermittently")
        resolved.append(inc)
    return resolved


def _get_for_update(s: Session, incident_id: int) -> Incident:
    inc = s.get(Incident, incident_id, with_for_update=True)
    if inc is None:
        raise IncidentNotFound(incident_id)
    return inc


def acknowledge(s: Session, incident_id: int, user: str) -> Incident:
    """Acknowledging twice is harmless (idempotent). A resolved incident cannot be acknowledged."""
    inc = _get_for_update(s, incident_id)
    if inc.status == "RESOLVED":
        raise IncidentAlreadyResolved(incident_id)
    if inc.status == "OPEN":
        inc.status = "ACKNOWLEDGED"
        inc.acknowledged_at = utcnow()
        inc.acknowledged_by = user
        add_event(s, inc, "acknowledged", f"Acknowledged by {user}")
    return inc


def add_note(s: Session, incident_id: int, user: str, text: str) -> IncidentNote:
    """Notes are internal only. Notes on resolved incidents are rejected (D10)."""
    inc = _get_for_update(s, incident_id)
    if inc.status == "RESOLVED":
        raise IncidentAlreadyResolved(incident_id)
    note = IncidentNote(incident_id=inc.id, author=user, note=text.strip(), created_at=utcnow())
    s.add(note)
    add_event(s, inc, "note", f"Note by {user}")
    return note
