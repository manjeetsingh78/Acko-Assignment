"""All tunable settings in one place. Each can be overridden by an environment variable.

Values follow DECISIONS.md (D3-D7). Change a rule here, not in the code that uses it.
"""
import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    db_path: str = os.environ.get("PULSE_DB_PATH", "data/pulse.db")
    webhook_url: str = os.environ.get("PULSE_WEBHOOK_URL", "http://localhost:9900/pulse")
    seed_services: bool = os.environ.get("PULSE_SEED", "1") == "1"
    # Base URL for seeded services: "http://{name}:8080" inside Docker.
    seed_url_template: str = os.environ.get("PULSE_SEED_URL_TEMPLATE", "http://{name}:8080/health")

    # Catalogue defaults and limits (D3, D4)
    default_interval_s: int = _int("PULSE_DEFAULT_INTERVAL_S", 10)
    min_interval_s: int = _int("PULSE_MIN_INTERVAL_S", 6)
    max_interval_s: int = _int("PULSE_MAX_INTERVAL_S", 60)
    default_timeout_ms: int = _int("PULSE_DEFAULT_TIMEOUT_MS", 5000)
    default_slo_target: float = float(os.environ.get("PULSE_DEFAULT_SLO_TARGET", 99.9))

    # State rules (D5-D7)
    slow_threshold_ms: int = _int("PULSE_SLOW_MS", 1000)
    failures_to_down: int = _int("PULSE_FAILURES_TO_DOWN", 2)
    successes_to_recover: int = _int("PULSE_SUCCESSES_TO_RECOVER", 2)


settings = Settings()
