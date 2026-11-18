"""All tunable settings in one place. Each can be overridden by an environment variable.

Values follow DECISIONS.md (D19-D26). Change a rule here, not in the code that uses it.
"""
import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


# D20: the check interval comes from the service's criticality tier, never from its name.
# Every value is only a default: an operator can override it per service.
TIER_INTERVAL_SECONDS = {
    "critical": 10,
    "high": 15,
    "medium": 20,
    "low": 30,
}

# D33: incident severity follows criticality.
TIER_SEVERITY = {"critical": "SEV1", "high": "SEV2", "medium": "SEV3", "low": "SEV4"}
# Flaky incidents are one step less severe than a full outage of the same service (D49).
FLAKY_SEVERITY = {"critical": "SEV2", "high": "SEV3", "medium": "SEV4", "low": "SEV4"}


def default_flaky_window(failure_count: int, interval_s: int) -> int:
    """D26: the window must hold enough checks for the flaky rule to be able to fire."""
    return max(60, 2 * failure_count * interval_s)


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "PULSE_DATABASE_URL", "postgresql+psycopg://pulse:pulse@127.0.0.1:5433/pulse")
    webhook_url: str = os.environ.get("PULSE_WEBHOOK_URL", "http://localhost:9900/pulse")
    seed_services: bool = os.environ.get("PULSE_SEED", "1") == "1"
    # Inside Docker, Pulse reaches the kit by service name, e.g. http://claims:8080/health
    seed_url_template: str = os.environ.get("PULSE_SEED_URL_TEMPLATE", "http://{name}:8080/health")

    # Check defaults (D21, D22)
    default_timeout_ms: int = _int("PULSE_DEFAULT_TIMEOUT_MS", 5000)
    default_slow_threshold_ms: int = _int("PULSE_DEFAULT_SLOW_MS", 1000)

    # State rules (PO: 2 failures -> DOWN, 3 successes -> recovered)
    default_failure_threshold: int = _int("PULSE_FAILURE_THRESHOLD", 2)
    default_recovery_threshold: int = _int("PULSE_RECOVERY_THRESHOLD", 3)
    default_flaky_failure_count: int = _int("PULSE_FLAKY_FAILURE_COUNT", 3)

    default_reliability_target: float = float(os.environ.get("PULSE_DEFAULT_TARGET", 99.9))

    tier_intervals: dict = field(default_factory=lambda: dict(TIER_INTERVAL_SECONDS))

    # Background work (scheduler + notifier). Tests turn it off and drive things directly.
    run_background: bool = os.environ.get("PULSE_RUN_BACKGROUND", "1") == "1"
    max_concurrent_checks: int = _int("PULSE_MAX_CONCURRENT_CHECKS", 10)
    supervisor_interval_s: float = float(os.environ.get("PULSE_SUPERVISOR_INTERVAL_S", 5))
    notifier_poll_s: float = float(os.environ.get("PULSE_NOTIFIER_POLL_S", 2))
    webhook_timeout_s: float = float(os.environ.get("PULSE_WEBHOOK_TIMEOUT_S", 5))

    # Reliability (D32)
    availability_window_hours: int = _int("PULSE_AVAILABILITY_WINDOW_HOURS", 24)
    min_checks_for_availability: int = _int("PULSE_MIN_CHECKS", 10)

    # Auth (D31). The defaults are for local demos only; docker-compose sets them explicitly.
    operator_username: str = os.environ.get("PULSE_OPERATOR_USERNAME", "operator")
    operator_password: str = os.environ.get("PULSE_OPERATOR_PASSWORD", "pulse-demo")
    session_secret: str = os.environ.get("PULSE_SESSION_SECRET", "dev-only-change-me")



settings = Settings()
