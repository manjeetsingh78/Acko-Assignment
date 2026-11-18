"""Seeds the catalogue with the 4 starter-kit services, only if the catalogue is empty.

Running it again (or restarting) never duplicates or overwrites services an operator has edited.
Criticality is seed DATA here; no code anywhere decides behaviour from a service's name.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, default_flaky_window
from app.models import Service

# (name, owner, criticality, description) - criticality as proposed by the PO
KIT_SERVICES = [
    ("quotes", "quotes-team", "medium", "Insurance quote generation"),
    ("payments", "payments-team", "critical", "Premium payments"),
    ("claims", "claims-team", "high", "Claim submission and tracking"),
    ("notifications", "notifications-team", "low", "Customer emails and SMS"),
]


def build_service(name: str, owner: str, criticality: str, description: str, s: Settings) -> Service:
    """A Service filled with the defaults for its criticality tier."""
    interval = s.tier_intervals[criticality]
    return Service(
        name=name,
        description=description,
        health_url=s.seed_url_template.format(name=name),
        owner=owner,
        criticality=criticality,
        check_interval_seconds=interval,
        timeout_ms=s.default_timeout_ms,
        slow_threshold_ms=s.default_slow_threshold_ms,
        failure_threshold=s.default_failure_threshold,
        recovery_threshold=s.default_recovery_threshold,
        flaky_enabled=True,
        flaky_opens_incident=False,
        flaky_failure_count=s.default_flaky_failure_count,
        flaky_window_seconds=default_flaky_window(s.default_flaky_failure_count, interval),
        reliability_target=s.default_reliability_target,
    )


def seed_catalogue(session: Session, s: Settings) -> int:
    """Insert the kit services if no services exist. Returns how many were inserted."""
    if session.scalar(select(func.count()).select_from(Service)) > 0:
        return 0
    session.add_all(build_service(*row, s) for row in KIT_SERVICES)
    session.commit()
    return len(KIT_SERVICES)
