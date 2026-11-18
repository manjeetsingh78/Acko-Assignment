"""All human-facing text: customer-safe public wording and webhook payloads. Pure functions.

D30: public text is built ONLY from these fixed templates. Nothing here ever receives the raw
error message from a health check, so internal details (IPs, DB hosts) cannot leak.
"""
from datetime import datetime

from app.domain.classify import Outcome

PUBLIC_LABEL = {
    "HEALTHY": "Operational",
    "DEGRADED": "Degraded performance",
    "DOWN": "Service disruption",
    "RECOVERING": "Recovering",
    "UNKNOWN": "No data yet",
}


def display_name(service_name: str) -> str:
    return service_name.replace("-", " ").replace("_", " ").title()


def public_status_message(service_name: str, status: str) -> str:
    name = display_name(service_name)
    return {
        "HEALTHY": f"{name} is operating normally.",
        "DEGRADED": f"{name} is experiencing delays. Some requests may be slower than usual or fail intermittently.",
        "DOWN": f"{name} is currently unavailable for some customers. Our engineering team is investigating the issue.",
        "RECOVERING": f"A fix is in place and {name} is recovering. We are monitoring it closely.",
        "UNKNOWN": f"We have not collected status information for {name} yet.",
    }[status]


def public_incident_message(service_name: str) -> str:
    name = display_name(service_name)
    return (f"{name} is currently experiencing problems. Some customers may be unable to use it. "
            "Our engineering team is investigating the issue.")


def public_flaky_incident_message(service_name: str) -> str:
    name = display_name(service_name)
    return (f"{name} is experiencing intermittent errors. Some requests may fail; trying again usually works. "
            "Our engineering team is investigating.")


PUBLIC_UPDATE = {
    "opened": "Investigating: we are aware of a problem with {name} and are looking into it.",
    "recovering": "Monitoring: {name} is responding again. We are confirming the recovery.",
    "resolved": "Resolved: {name} has recovered and is operating normally.",
    "flaky_opened": "Investigating: some requests to {name} are failing intermittently.",
    "flaky_resolved": "Resolved: requests to {name} are succeeding consistently again.",
}


def public_update(kind: str, service_name: str) -> str:
    return PUBLIC_UPDATE[kind].format(name=display_name(service_name))


def describe_failure(outcome: Outcome, http_status: int | None, timeout_ms: int) -> str:
    """A short failure CATEGORY for engineers and webhooks. Never the raw error body."""
    if outcome == Outcome.FAIL_TIMEOUT:
        return f"timed out after {timeout_ms} ms"
    if outcome == Outcome.FAIL_CONNECTION:
        return "connection failed"
    return f"HTTP {http_status}"


def _iso(ts: datetime) -> str:
    return ts.isoformat().replace("+00:00", "Z")


def incident_code(incident_id: int) -> str:
    return f"INC-{incident_id:04d}"


def webhook_payload(event: str, *, notification_id: int, incident_id: int, service: str, severity: str,
                    status: str, message: str, started_at: datetime, timestamp: datetime,
                    resolved_at: datetime | None = None) -> dict:
    payload = {
        "event_id": f"ntf-{notification_id}",
        "event": event,
        "incident_id": incident_code(incident_id),
        "service": service,
        "severity": severity,
        "status": status,
        "message": message,
        "started_at": _iso(started_at),
        "timestamp": _iso(timestamp),
    }
    if resolved_at is not None:
        payload["resolved_at"] = _iso(resolved_at)
        payload["duration_seconds"] = int((resolved_at - started_at).total_seconds())
    return payload
