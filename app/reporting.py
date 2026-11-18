"""Read-side queries: reliability per service, dashboard and product metrics, and the public view.

Every number is computed from stored rows. Nothing is invented: when there is no data the
value is None and the UI says so.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import utcnow
from app.domain import messages
from app.domain.reliability import availability, error_budget, percentile
from app.models import (HealthCheck, Incident, IncidentEvent, IncidentNote, Notification, Service,
                        ServiceStateChange)
from app.schemas import (IncidentDetail, IncidentOut, NotificationOut, PublicHistory, PublicIncident,
                         PublicService, PublicStatus, PublicUpdate, TimelineEntry)


def _seconds(expr):
    return func.extract("epoch", expr)


# ---------------- per-service reliability ----------------
def service_reliability(s: Session, service: Service, settings: Settings, now: datetime | None = None,
                        window_hours: int | None = None) -> dict:
    """Reliability over a rolling window: 24 h by default, 168 h (7 days) on request (PO R9)."""
    now = now or utcnow()
    window_hours = window_hours or settings.availability_window_hours
    window_minutes = window_hours * 60
    since = now - timedelta(minutes=window_minutes)
    row = s.execute(select(func.count(), func.count().filter(HealthCheck.success.is_(True)),
                           func.min(HealthCheck.started_at))
                    .where(HealthCheck.service_id == service.id, HealthCheck.started_at > since)).one()
    total, successful, first_at = row
    covered = (now - first_at).total_seconds() / 60 if first_at else 0.0
    avail = availability(total, successful, covered, window_minutes, settings.min_checks_for_availability)
    budget = error_budget(float(service.reliability_target), avail)

    latencies = list(s.scalars(select(HealthCheck.response_time_ms).where(
        HealthCheck.service_id == service.id, HealthCheck.started_at > since, HealthCheck.success.is_(True))))
    flaky_episodes = s.scalar(select(func.count()).select_from(ServiceStateChange).where(
        ServiceStateChange.service_id == service.id, ServiceStateChange.reason == "flaky",
        ServiceStateChange.changed_at > since))
    return {
        "window_hours": window_hours,
        "availability": avail,
        "error_budget": budget,
        "target": float(service.reliability_target),
        "meets_target": (avail.percent >= float(service.reliability_target)) if avail.sufficient else None,
        "latency_avg_ms": round(sum(latencies) / len(latencies)) if latencies else None,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "flaky_episodes": flaky_episodes,
    }


def recent_checks(s: Session, service_id: int, limit: int = 100) -> list[HealthCheck]:
    rows = list(s.scalars(select(HealthCheck).where(HealthCheck.service_id == service_id)
                          .order_by(HealthCheck.started_at.desc()).limit(limit)))
    return rows


# ---------------- incidents ----------------
def incident_out(inc: Incident, service_name: str) -> IncidentOut:
    data = {c: getattr(inc, c) for c in IncidentOut.model_fields if hasattr(inc, c)}
    return IncidentOut(**data, code=messages.incident_code(inc.id), service_name=service_name)


def list_incidents(s: Session, status: str | None = None, service_id: int | None = None,
                   limit: int = 100) -> list[IncidentOut]:
    q = select(Incident, Service.name).join(Service).order_by(Incident.opened_at.desc()).limit(limit)
    if status == "active":
        q = q.where(Incident.status != "RESOLVED")
    elif status:
        q = q.where(Incident.status == status.upper())
    if service_id:
        q = q.where(Incident.service_id == service_id)
    return [incident_out(inc, name) for inc, name in s.execute(q)]


def incident_detail(s: Session, inc: Incident) -> IncidentDetail:
    service = s.get(Service, inc.service_id)
    timeline = [TimelineEntry(at=e.created_at, kind=e.event_type, text=e.description, is_public=e.is_public,
                              details=e.details)
                for e in s.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id))
                if e.event_type != "note"]
    timeline += [TimelineEntry(at=n.created_at, kind="note", text=f"{n.author}: {n.note}")
                 for n in s.scalars(select(IncidentNote).where(IncidentNote.incident_id == inc.id))]
    timeline.sort(key=lambda t: t.at)
    notes = [NotificationOut.model_validate(n) for n in s.scalars(
        select(Notification).where(Notification.incident_id == inc.id).order_by(Notification.id))]
    base = incident_out(inc, service.name)
    return IncidentDetail(**base.model_dump(), timeline=timeline, notifications=notes)


# ---------------- dashboard + product metrics ----------------
def dashboard_summary(s: Session) -> dict:
    counts = dict(s.execute(select(Service.current_status, func.count())
                            .where(Service.enabled.is_(True)).group_by(Service.current_status)).all())
    return {
        "total_services": sum(counts.values()),
        "by_status": {st: counts.get(st, 0) for st in ("HEALTHY", "DEGRADED", "DOWN", "RECOVERING", "UNKNOWN")},
        "active_incidents": s.scalar(select(func.count()).select_from(Incident)
                                     .where(Incident.status != "RESOLVED")),
    }


def _avg_seconds(s: Session, expr, *where) -> float | None:
    value = s.scalar(select(func.avg(_seconds(expr))).where(*where))
    return round(float(value), 1) if value is not None else None


def metrics_summary(s: Session, now: datetime | None = None) -> dict:
    """Must-have 9: how quickly problems are detected and resolved, and how checking performs."""
    now = now or utcnow()
    day_ago, hour_ago = now - timedelta(hours=24), now - timedelta(hours=1)

    checks = s.execute(select(
        func.count(),
        func.count().filter(HealthCheck.success.is_(True)),
        func.avg(HealthCheck.response_time_ms).filter(HealthCheck.success.is_(True)),
    ).where(HealthCheck.started_at > day_ago)).one()
    lags = [float(x) for x in s.scalars(select(_seconds(HealthCheck.started_at - HealthCheck.scheduled_at))
                                        .where(HealthCheck.started_at > hour_ago))]
    durations = [float(x) for x in s.scalars(select(_seconds(HealthCheck.completed_at - HealthCheck.started_at))
                                             .where(HealthCheck.started_at > hour_ago))]
    deliveries = dict(s.execute(select(Notification.delivery_status, func.count())
                                .group_by(Notification.delivery_status)).all())
    finished = deliveries.get("DELIVERED", 0) + deliveries.get("FAILED", 0)
    total_incidents = s.scalar(select(func.count()).select_from(Incident))

    return {
        "incidents_total": total_incidents,
        # MTTD: first failed check -> incident opened (detection delay caused by our rules)
        "mttd_seconds": _avg_seconds(s, Incident.opened_at - Incident.first_failure_at),
        # MTTA / MTTR exclude incidents not yet acknowledged / resolved (PagerDuty definitions)
        "mtta_seconds": _avg_seconds(s, Incident.acknowledged_at - Incident.opened_at,
                                     Incident.acknowledged_at.is_not(None)),
        "mttr_seconds": _avg_seconds(s, Incident.resolved_at - Incident.opened_at,
                                     Incident.resolved_at.is_not(None)),
        "checks_24h": checks[0],
        "check_success_rate_24h": round(checks[1] / checks[0] * 100, 2) if checks[0] else None,
        "avg_response_ms_24h": round(float(checks[2])) if checks[2] is not None else None,
        "check_duration_avg_ms_1h": round(sum(durations) / len(durations) * 1000) if durations else None,
        "scheduler_lag_p95_ms_1h": round(percentile(lags, 95) * 1000) if lags else None,
        "notifications": {"delivered": deliveries.get("DELIVERED", 0), "failed": deliveries.get("FAILED", 0),
                          "pending": deliveries.get("PENDING", 0)},
        "notification_success_rate": round(deliveries.get("DELIVERED", 0) / finished * 100, 1) if finished else None,
        "flaky_episodes_24h": s.scalar(select(func.count()).select_from(ServiceStateChange).where(
            ServiceStateChange.reason == "flaky", ServiceStateChange.changed_at > day_ago)),
    }


def response_time_series(s: Session, service_id: int, limit: int = 60) -> list[tuple[datetime, int | None, bool]]:
    rows = s.execute(select(HealthCheck.started_at, HealthCheck.response_time_ms, HealthCheck.success)
                     .where(HealthCheck.service_id == service_id)
                     .order_by(HealthCheck.started_at.desc()).limit(limit)).all()
    return list(reversed(rows))


# ---------------- PUBLIC view (customer safe) ----------------
_PUBLIC_STATE = {"OPEN": "Investigating", "ACKNOWLEDGED": "Investigating", "RESOLVED": "Resolved"}


def _public_incident(s: Session, inc: Incident, service_name: str, service_status: str) -> PublicIncident:
    latest = s.scalars(select(IncidentEvent).where(IncidentEvent.incident_id == inc.id,
                                                   IncidentEvent.is_public.is_(True))
                       .order_by(IncidentEvent.created_at.desc(), IncidentEvent.id.desc())).first()
    state = _PUBLIC_STATE[inc.status]
    if inc.status != "RESOLVED" and service_status == "RECOVERING":
        state = "Recovering"
    duration = int((inc.resolved_at - inc.first_failure_at).total_seconds() // 60) if inc.resolved_at else None
    return PublicIncident(
        id=messages.incident_code(inc.id), service=messages.display_name(service_name),
        message=inc.public_message, state=state, started_at=inc.first_failure_at, resolved_at=inc.resolved_at,
        duration_minutes=duration,
        latest_update=PublicUpdate(at=latest.created_at, text=latest.description) if latest else None)


def public_status(s: Session) -> PublicStatus:
    services = list(s.scalars(select(Service).where(Service.enabled.is_(True)).order_by(Service.name)))
    out = []
    for svc in services:
        active = s.scalars(select(Incident).where(Incident.service_id == svc.id, Incident.status != "RESOLVED")
                           .order_by(Incident.opened_at)).all()
        out.append(PublicService(
            name=messages.display_name(svc.name), status=messages.PUBLIC_LABEL[svc.current_status],
            message=messages.public_status_message(svc.name, svc.current_status), since=svc.status_since,
            active_incidents=[_public_incident(s, i, svc.name, svc.current_status) for i in active]))
    statuses = {svc.current_status for svc in services}
    if "DOWN" in statuses or "RECOVERING" in statuses:
        overall = "Some services are experiencing a disruption"
    elif "DEGRADED" in statuses:
        overall = "Some services are running slower than usual"
    elif statuses == {"HEALTHY"}:
        overall = "All systems operational"
    else:
        overall = "Status is being collected"
    return PublicStatus(overall=overall, updated_at=utcnow(), services=out)


def public_history(s: Session, days: int = 7) -> PublicHistory:
    since = utcnow() - timedelta(days=days)
    rows = s.execute(select(Incident, Service.name, Service.current_status).join(Service)
                     .where(Incident.opened_at > since).order_by(Incident.opened_at.desc()).limit(50)).all()
    return PublicHistory(days=days, incidents=[_public_incident(s, i, name, st) for i, name, st in rows])
