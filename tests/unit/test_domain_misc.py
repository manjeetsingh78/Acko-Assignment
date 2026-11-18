"""Unit tests: reliability maths, config validation, public wording and webhook payloads."""
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.classify import Outcome
from app.domain.messages import PUBLIC_LABEL, describe_failure, public_status_message, webhook_payload
from app.domain.reliability import allowed_downtime_minutes, availability, error_budget, percentile
from app.domain.validation import config_errors

INTERNAL = "upstream payments-db timeout at 10.12.4.7:5432"  # the kit's internal error text


# ---------- reliability ----------
def test_allowed_downtime_for_three_nines_over_a_day_is_1_44_minutes():
    assert allowed_downtime_minutes(99.9, 24 * 60) == pytest.approx(1.44)


def test_availability_percentage():
    a = availability(100, 98, covered_minutes=60, window_minutes=1440)
    assert a.percent == 98.0 and a.sufficient


def test_no_checks_is_insufficient_data_not_100_percent():
    a = availability(0, 0, 0, 1440)
    assert a.percent is None and not a.sufficient
    assert error_budget(99.9, a) is None


def test_few_checks_are_flagged_as_insufficient():
    assert not availability(5, 5, 1, 1440).sufficient


def test_error_budget_remaining_and_exhausted():
    # 1000 minutes covered, target 99 % -> 10 min allowed. 99.5 % -> 5 min used.
    b = error_budget(99.0, availability(1000, 995, 1000, 1440))
    assert (b.allowed_minutes, b.used_minutes, b.remaining_minutes) == pytest.approx((10, 5, 5))
    assert not b.exhausted and b.remaining_percent == 50.0
    worse = error_budget(99.0, availability(100, 90, 1000, 1440))
    assert worse.exhausted and worse.remaining_minutes < 0


def test_percentile():
    assert percentile([], 95) is None
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile(list(range(1, 101)), 95) == 95


# ---------- config validation ----------
def valid_config(**overrides):
    c = dict(criticality="critical", health_url="http://payments:8080/health", check_interval_seconds=10,
             timeout_ms=5000, slow_threshold_ms=1000, failure_threshold=2, recovery_threshold=3,
             flaky_failure_count=3, flaky_window_seconds=60, reliability_target=99.9)
    c.update(overrides)
    return c


def test_valid_config_has_no_errors():
    assert config_errors(valid_config()) == []


@pytest.mark.parametrize("overrides, fragment", [
    ({"timeout_ms": 10_000}, "timeout_ms must be shorter than the check interval"),
    ({"slow_threshold_ms": 5000}, "slow_threshold_ms"),
    ({"flaky_window_seconds": 20}, "flaky_window_seconds"),      # 3 failures can't fit in 20 s at 10 s
    ({"criticality": "urgent"}, "criticality"),
    ({"reliability_target": 100}, "reliability_target"),
    ({"health_url": "claims:8080"}, "health_url"),
    ({"failure_threshold": 0}, "failure_threshold"),
])
def test_invalid_configs_are_rejected(overrides, fragment):
    assert any(fragment in e for e in config_errors(valid_config(**overrides)))


# ---------- public wording and webhooks never carry internal detail ----------
@pytest.mark.parametrize("status", list(PUBLIC_LABEL))
def test_public_messages_exist_for_every_state_and_are_customer_safe(status):
    msg = public_status_message("payments", status)
    assert "Payments" in msg and "10.12" not in msg and "db" not in msg.lower()


def test_failure_description_is_a_category_not_the_raw_error():
    assert describe_failure(Outcome.FAIL_HTTP, 503, 5000) == "HTTP 503"
    assert describe_failure(Outcome.FAIL_TIMEOUT, None, 5000) == "timed out after 5000 ms"
    assert describe_failure(Outcome.FAIL_CONNECTION, None, 5000) == "connection failed"


def test_webhook_payload_shapes():
    start = datetime(2026, 11, 18, 13, 0, 0, tzinfo=timezone.utc)
    opened = webhook_payload("incident.opened", notification_id=7, incident_id=3, service="payments",
                             severity="SEV1", status="DOWN", message="payments failed (HTTP 503)",
                             started_at=start, timestamp=start)
    assert opened["incident_id"] == "INC-0003" and opened["event_id"] == "ntf-7"
    assert opened["started_at"] == "2026-11-18T13:00:00Z" and "resolved_at" not in opened
    resolved = webhook_payload("incident.resolved", notification_id=8, incident_id=3, service="payments",
                               severity="SEV1", status="HEALTHY", message="recovered", started_at=start,
                               timestamp=start, resolved_at=start + timedelta(minutes=4))
    assert resolved["duration_seconds"] == 240
    assert INTERNAL not in str(opened) + str(resolved)
