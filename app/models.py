"""Database tables (DESIGN.md section 6). One class per table.

Business rules that must never be broken are also enforced here with CHECK and UNIQUE
constraints, so even a bug in the application code cannot store invalid data.
"""
from datetime import datetime

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SERVICE_STATUSES = ("UNKNOWN", "HEALTHY", "DEGRADED", "DOWN", "RECOVERING")
CRITICALITIES = ("critical", "high", "medium", "low")
CHECK_OUTCOMES = ("OK_FAST", "OK_SLOW", "FAIL_HTTP", "FAIL_TIMEOUT", "FAIL_CONNECTION")
INCIDENT_STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED")


def _in(column: str, values: tuple) -> str:
    """Build a CHECK expression like: status IN ('A', 'B')."""
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


def _now_column() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    health_url: Mapped[str] = mapped_column(String(500))
    owner: Mapped[str] = mapped_column(String(100))
    criticality: Mapped[str] = mapped_column(String(10))

    # Monitoring configuration (all per service, D20-D26)
    check_interval_seconds: Mapped[int] = mapped_column(Integer)
    timeout_ms: Mapped[int] = mapped_column(Integer)
    slow_threshold_ms: Mapped[int] = mapped_column(Integer)
    failure_threshold: Mapped[int] = mapped_column(Integer)
    recovery_threshold: Mapped[int] = mapped_column(Integer)
    flaky_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # D49: off by default (PO: flaky = DEGRADED only, until clarified). On = open a lower-severity incident.
    flaky_opens_incident: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    flaky_failure_count: Mapped[int] = mapped_column(Integer)
    flaky_window_seconds: Mapped[int] = mapped_column(Integer)
    reliability_target: Mapped[float] = mapped_column(Numeric(6, 3))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Current state, persisted so it survives a restart (R11)
    current_status: Mapped[str] = mapped_column(String(12), default="UNKNOWN")
    status_reason: Mapped[str | None] = mapped_column(String(50))
    status_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_successes: Mapped[int] = mapped_column(Integer, default=0)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = _now_column()
    updated_at: Mapped[datetime] = _now_column()

    __table_args__ = (
        CheckConstraint(_in("criticality", CRITICALITIES), name="ck_service_criticality"),
        CheckConstraint(_in("current_status", SERVICE_STATUSES), name="ck_service_status"),
        CheckConstraint("check_interval_seconds >= 1", name="ck_service_interval"),
        # D21: slow < timeout < interval, so "slow" and "failed" never overlap and checks never overlap.
        CheckConstraint("slow_threshold_ms > 0 AND slow_threshold_ms < timeout_ms", name="ck_service_slow_lt_timeout"),
        CheckConstraint("timeout_ms < check_interval_seconds * 1000", name="ck_service_timeout_lt_interval"),
        CheckConstraint("failure_threshold >= 1 AND recovery_threshold >= 1", name="ck_service_thresholds"),
        CheckConstraint("flaky_failure_count >= 2 AND flaky_window_seconds >= 1", name="ck_service_flaky"),
        CheckConstraint("reliability_target > 0 AND reliability_target < 100", name="ck_service_target"),
    )


class HealthCheck(Base):
    """Every check result, never edited. error_message is OPERATOR ONLY (it may contain internal IPs)."""
    __tablename__ = "health_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))   # lag = started - scheduled
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))   # duration = completed - started
    outcome: Mapped[str] = mapped_column(String(16))
    success: Mapped[bool] = mapped_column(Boolean)
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_time_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    resulting_status: Mapped[str] = mapped_column(String(12))

    __table_args__ = (
        CheckConstraint(_in("outcome", CHECK_OUTCOMES), name="ck_check_outcome"),
        Index("ix_checks_service_started", "service_id", "started_at"),
    )


class ServiceStateChange(Base):
    """One row per status change: used for flaky frequency and public history."""
    __tablename__ = "service_state_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    from_status: Mapped[str] = mapped_column(String(12))
    to_status: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str | None] = mapped_column(String(50))
    changed_at: Mapped[datetime] = _now_column()

    __table_args__ = (Index("ix_state_changes_service_time", "service_id", "changed_at"),)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)   # shown as INC-0001
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id", ondelete="CASCADE"))
    trigger_type: Mapped[str] = mapped_column(String(20))           # e.g. service_down (D28)
    # D27: e.g. "down:3:1842" (service 3, first failed check 1842). UNIQUE = no duplicate incident per trigger.
    trigger_key: Mapped[str] = mapped_column(String(100), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    severity: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(12), default="OPEN")
    public_message: Mapped[str] = mapped_column(Text)               # customer-safe template text
    first_failure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # for MTTD
    opened_at: Mapped[datetime] = _now_column()
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Note: deliberately NO "one unresolved incident per service" constraint (PO answer, D27).
    __table_args__ = (
        CheckConstraint(_in("status", INCIDENT_STATUSES), name="ck_incident_status"),
        CheckConstraint(_in("severity", ("SEV1", "SEV2", "SEV3", "SEV4")), name="ck_incident_severity"),
        Index("ix_incidents_service_status", "service_id", "status"),
    )


class IncidentEvent(Base):
    """The incident timeline. Only events with is_public=True may ever appear on the status page."""
    __tablename__ = "incident_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(30))
    description: Mapped[str] = mapped_column(Text)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
    details: Mapped[dict | None] = mapped_column("metadata", JSONB)  # http_status, error, check_id
    created_at: Mapped[datetime] = _now_column()

    __table_args__ = (Index("ix_events_incident_time", "incident_id", "created_at"),)


class IncidentNote(Base):
    __tablename__ = "incident_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    author: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_column()


class Notification(Base):
    """Webhook outbox and delivery history (D29)."""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"))
    event_type: Mapped[str] = mapped_column(String(30))     # incident.opened | incident.resolved
    payload: Mapped[dict] = mapped_column(JSONB)
    delivery_status: Mapped[str] = mapped_column(String(10), default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = _now_column()
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_http_status: Mapped[int | None] = mapped_column(Integer)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_column()

    __table_args__ = (
        UniqueConstraint("incident_id", "event_type", name="uq_notification_once_per_event"),
        CheckConstraint(_in("delivery_status", ("PENDING", "DELIVERED", "FAILED")), name="ck_notification_status"),
        CheckConstraint(_in("event_type", ("incident.opened", "incident.resolved")), name="ck_notification_event"),
    )
