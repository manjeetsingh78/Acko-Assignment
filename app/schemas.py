"""API request and response models.

D30: the PUBLIC models below have no field that could carry technical detail (no error message,
HTTP status, URL, owner or note). Public endpoints can only return these models, so a leak would
need a code change here, which a reviewer would see.
"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


# ---------------- operator: services ----------------
class ServiceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    description: str = ""
    health_url: str = Field(max_length=500)
    owner: str = Field(min_length=1, max_length=100)
    criticality: str = "medium"
    # Optional: when omitted, the criticality tier's defaults are used.
    check_interval_seconds: int | None = None
    timeout_ms: int | None = None
    slow_threshold_ms: int | None = None
    failure_threshold: int | None = None
    recovery_threshold: int | None = None
    flaky_enabled: bool = True
    flaky_opens_incident: bool = False
    flaky_failure_count: int | None = None
    flaky_window_seconds: int | None = None
    reliability_target: float | None = None


class ServiceUpdate(BaseModel):
    description: str | None = None
    health_url: str | None = Field(default=None, max_length=500)
    owner: str | None = None
    criticality: str | None = None
    check_interval_seconds: int | None = None
    timeout_ms: int | None = None
    slow_threshold_ms: int | None = None
    failure_threshold: int | None = None
    recovery_threshold: int | None = None
    flaky_enabled: bool | None = None
    flaky_opens_incident: bool | None = None
    flaky_failure_count: int | None = None
    flaky_window_seconds: int | None = None
    reliability_target: float | None = None
    enabled: bool | None = None


class OperatorService(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: str
    health_url: str
    owner: str
    criticality: str
    check_interval_seconds: int
    timeout_ms: int
    slow_threshold_ms: int
    failure_threshold: int
    recovery_threshold: int
    flaky_enabled: bool
    flaky_opens_incident: bool
    flaky_failure_count: int
    flaky_window_seconds: int
    reliability_target: float
    enabled: bool
    current_status: str
    status_reason: str | None
    status_since: datetime | None
    consecutive_failures: int
    consecutive_successes: int
    last_checked_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None


class HealthCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    scheduled_at: datetime
    started_at: datetime
    completed_at: datetime
    outcome: str
    success: bool
    http_status: int | None
    response_time_ms: int | None
    error_message: str | None        # exact technical error: operator only
    resulting_status: str


# ---------------- operator: incidents ----------------
class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    service_id: int
    service_name: str
    trigger_type: str
    trigger_key: str
    title: str
    severity: str
    status: str
    first_failure_at: datetime
    opened_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None


class TimelineEntry(BaseModel):
    at: datetime
    kind: str
    text: str
    is_public: bool = False
    details: dict | None = None


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_type: str
    delivery_status: str
    attempts: int
    last_attempt_at: datetime | None
    last_http_status: int | None
    last_error: str | None


class IncidentDetail(IncidentOut):
    timeline: list[TimelineEntry]
    notifications: list[NotificationOut]


class NoteIn(BaseModel):
    note: str = Field(min_length=1, max_length=2000)


class LoginIn(BaseModel):
    username: str
    password: str


# ---------------- PUBLIC (customer-safe) ----------------
class PublicUpdate(BaseModel):
    at: datetime
    text: str


class PublicIncident(BaseModel):
    id: str                       # INC-0001
    service: str
    message: str
    state: str                    # "Investigating" | "Recovering" | "Resolved"
    started_at: datetime
    resolved_at: datetime | None
    duration_minutes: int | None
    latest_update: PublicUpdate | None


class PublicService(BaseModel):
    name: str
    status: str                   # customer label, e.g. "Service disruption"
    message: str
    since: datetime | None
    active_incidents: list[PublicIncident]


class PublicStatus(BaseModel):
    overall: str
    updated_at: datetime
    services: list[PublicService]


class PublicHistory(BaseModel):
    days: int
    incidents: list[PublicIncident]
