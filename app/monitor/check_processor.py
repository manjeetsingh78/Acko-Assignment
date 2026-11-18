"""Stores one check result and applies its consequences, in ONE database transaction.

Steps: save the check -> count recent failures (flaky) -> run the pure state machine ->
update the service -> open/resolve incidents and queue webhooks. If anything fails, nothing is saved.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app import incidents
from app.domain.classify import classify, is_success
from app.domain.messages import describe_failure
from app.domain.state import OPEN_FLAKY, RESOLVE_FLAKY, Rules, flaky_incident_action, next_state
from app.models import HealthCheck, Service, ServiceStateChange
from app.monitor.probe import ProbeResult

log = logging.getLogger("pulse.checks")


def _failures_in_window(s: Session, service: Service, now: datetime) -> int:
    """Intermittent failures in the flaky window (D41).

    Failures recorded while the service was DOWN are excluded: the PO defines flaky as failing
    repeatedly "but not continuously down", and otherwise a service would look flaky for a whole
    window right after recovering from a real outage.
    """
    since = now - timedelta(seconds=service.flaky_window_seconds)
    return s.scalar(select(func.count()).select_from(HealthCheck).where(
        HealthCheck.service_id == service.id, HealthCheck.success.is_(False),
        HealthCheck.resulting_status != "DOWN", HealthCheck.started_at > since))


def _first_failure_of_streak(s: Session, service_id: int, streak_length: int) -> HealthCheck:
    """The oldest of the last `streak_length` checks = the first failure of the current streak."""
    recent = list(s.scalars(select(HealthCheck).where(HealthCheck.service_id == service_id)
                            .order_by(HealthCheck.started_at.desc(), HealthCheck.id.desc())
                            .limit(streak_length)))
    return recent[-1]


def process_check(sessions: sessionmaker, service_id: int, result: ProbeResult, scheduled_at: datetime) -> str | None:
    """Returns the service's new status (None if the service no longer exists)."""
    with sessions() as s, s.begin():
        # Row lock: an operator PATCH or a second writer cannot interleave with this update.
        service = s.get(Service, service_id, with_for_update=True)
        if service is None:
            return None

        outcome = classify(result.http_status, result.response_time_ms, result.error_kind,
                           service.slow_threshold_ms)
        check = HealthCheck(
            service_id=service.id, scheduled_at=scheduled_at, started_at=result.started_at,
            completed_at=result.completed_at, outcome=outcome.value, success=is_success(outcome),
            http_status=result.http_status, response_time_ms=result.response_time_ms,
            error_message=result.error_message, resulting_status=service.current_status)
        s.add(check)
        s.flush()

        failures_in_window = _failures_in_window(s, service, result.started_at)
        is_flaky = service.flaky_enabled and failures_in_window >= service.flaky_failure_count

        t = next_state(service.current_status, service.consecutive_failures, service.consecutive_successes,
                       outcome, is_flaky, Rules(service.failure_threshold, service.recovery_threshold))

        check.resulting_status = t.status
        previous_status = service.current_status
        if t.status != previous_status or t.reason != service.status_reason:
            s.add(ServiceStateChange(service_id=service.id, from_status=previous_status, to_status=t.status,
                                     reason=t.reason, changed_at=result.started_at))
        if t.status != previous_status:
            service.status_since = result.started_at
        service.current_status = t.status
        service.status_reason = t.reason
        service.consecutive_failures = t.consecutive_failures
        service.consecutive_successes = t.consecutive_successes
        service.last_checked_at = result.started_at
        if check.success:
            service.last_success_at = result.started_at
        else:
            service.last_failure_at = result.started_at

        summary = describe_failure(outcome, result.http_status, service.timeout_ms)
        if t.open_incident:
            first = _first_failure_of_streak(s, service.id, t.consecutive_failures)
            inc = incidents.open_down_incident(s, service, first, check, summary)
            log.warning("%s DOWN -> incident %s opened", service.name, inc.id)
        if t.recovery_interrupted:
            incidents.record_recovery_interrupted(s, service, check, summary)
        if t.recovery_progress:
            incidents.record_recovery_progress(s, service, check, t.consecutive_successes)
        if t.resolve_incidents:
            resolved = incidents.resolve_after_recovery(s, service, check, t.status)
            log.warning("%s recovered -> resolved incidents %s", service.name, [i.id for i in resolved])

        # Flaky incidents: a second, independent trigger (D49), decided by a pure function.
        action = flaky_incident_action(is_flaky, bool(incidents.unresolved_flaky_incidents(s, service.id)),
                                       service.flaky_opens_incident)
        if action == OPEN_FLAKY:
            inc = incidents.open_flaky_incident(s, service, check, failures_in_window)
            log.warning("%s flaky -> incident %s opened", service.name, inc.id)
        elif action == RESOLVE_FLAKY:
            incidents.resolve_flaky_incidents(s, service, check)

        if t.status != previous_status:
            log.info("%s %s -> %s (%s)", service.name, previous_status, t.status, t.reason)
        return t.status
