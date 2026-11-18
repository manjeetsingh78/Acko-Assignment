"""Service catalogue: create and update services with validation (must-have 1)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, default_flaky_window
from app.db import utcnow
from app.domain.validation import config_errors
from app.models import Service
from app.schemas import ServiceCreate, ServiceUpdate

CONFIG_FIELDS = ("criticality", "health_url", "check_interval_seconds", "timeout_ms", "slow_threshold_ms",
                 "failure_threshold", "recovery_threshold", "flaky_failure_count", "flaky_window_seconds",
                 "reliability_target")


class ConfigError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _validate(values: dict) -> None:
    errors = config_errors({k: values[k] for k in CONFIG_FIELDS})
    if errors:
        raise ConfigError(errors)


def create_service(s: Session, data: ServiceCreate, settings: Settings) -> Service:
    if s.scalar(select(Service.id).where(Service.name == data.name)):
        raise ConfigError([f"a service named '{data.name}' already exists"])
    if data.criticality not in settings.tier_intervals:
        raise ConfigError([f"criticality must be one of {sorted(settings.tier_intervals)}"])

    interval = data.check_interval_seconds or settings.tier_intervals[data.criticality]
    flaky_count = data.flaky_failure_count or settings.default_flaky_failure_count
    values = data.model_dump()
    values.update(
        check_interval_seconds=interval,
        timeout_ms=data.timeout_ms or settings.default_timeout_ms,
        slow_threshold_ms=data.slow_threshold_ms or settings.default_slow_threshold_ms,
        failure_threshold=data.failure_threshold or settings.default_failure_threshold,
        recovery_threshold=data.recovery_threshold or settings.default_recovery_threshold,
        flaky_failure_count=flaky_count,
        flaky_window_seconds=data.flaky_window_seconds or default_flaky_window(flaky_count, interval),
        reliability_target=data.reliability_target or settings.default_reliability_target,
    )
    _validate(values)
    service = Service(**values)
    s.add(service)
    s.flush()
    return service


def update_service(s: Session, service: Service, data: ServiceUpdate) -> Service:
    """Merge the change with the stored values, then validate the WHOLE result (not just the change)."""
    changes = data.model_dump(exclude_unset=True, exclude_none=True)
    merged = {f: getattr(service, f) for f in CONFIG_FIELDS} | changes
    _validate(merged)
    for key, value in changes.items():
        setattr(service, key, value)
    service.updated_at = utcnow()
    # New thresholds apply from the next check; the current state is not re-evaluated (DESIGN risk 14).
    return service
