"""Cross-field rules for a service's monitoring configuration. Pure function.

Used for both create and update (the update is merged with the stored values first), so a
PATCH can never produce a configuration that a POST would have rejected.
"""
from app.config import TIER_INTERVAL_SECONDS

MIN_INTERVAL_S = 2       # low enough for fast acceptance tests
MAX_INTERVAL_S = 3600


def config_errors(c: dict) -> list[str]:
    errors = []
    if c["criticality"] not in TIER_INTERVAL_SECONDS:
        errors.append(f"criticality must be one of {sorted(TIER_INTERVAL_SECONDS)}")
    if not str(c["health_url"]).startswith(("http://", "https://")):
        errors.append("health_url must start with http:// or https://")
    if not MIN_INTERVAL_S <= c["check_interval_seconds"] <= MAX_INTERVAL_S:
        errors.append(f"check_interval_seconds must be between {MIN_INTERVAL_S} and {MAX_INTERVAL_S}")
    if c["timeout_ms"] < 100:
        errors.append("timeout_ms must be at least 100")
    # D21: slow < timeout < interval. Otherwise "slow" can never happen, or checks would overlap.
    if c["timeout_ms"] >= c["check_interval_seconds"] * 1000:
        errors.append("timeout_ms must be shorter than the check interval")
    if not 0 < c["slow_threshold_ms"] < c["timeout_ms"]:
        errors.append("slow_threshold_ms must be greater than 0 and shorter than timeout_ms")
    if not 1 <= c["failure_threshold"] <= 10:
        errors.append("failure_threshold must be between 1 and 10")
    if not 1 <= c["recovery_threshold"] <= 10:
        errors.append("recovery_threshold must be between 1 and 10")
    if c["flaky_failure_count"] < 2:
        errors.append("flaky_failure_count must be at least 2")
    # D26: a window that cannot contain X checks makes the flaky rule impossible to trigger.
    if c["flaky_window_seconds"] < c["flaky_failure_count"] * c["check_interval_seconds"]:
        errors.append("flaky_window_seconds must be at least flaky_failure_count x check_interval_seconds")
    if not 0 < float(c["reliability_target"]) < 100:
        errors.append("reliability_target must be between 0 and 100 (exclusive)")
    return errors
