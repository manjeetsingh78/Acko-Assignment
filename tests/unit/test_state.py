"""Unit tests for classify() and next_state(): the core business rules (PO requirements 2-5)."""
import pytest

from app.domain.classify import Outcome, classify
from app.domain.state import DEGRADED, DOWN, HEALTHY, RECOVERING, UNKNOWN, Rules, next_state

OK, SLOW, FAIL = Outcome.OK_FAST, Outcome.OK_SLOW, Outcome.FAIL_HTTP
RULES = Rules(failure_threshold=2, recovery_threshold=3)


# ---------- classify ----------
@pytest.mark.parametrize("status, ms, kind, expected", [
    (200, 50, None, Outcome.OK_FAST),
    (200, 1000, None, Outcome.OK_FAST),       # exactly the threshold is still fast
    (204, 1500, None, Outcome.OK_SLOW),
    (503, 20, None, Outcome.FAIL_HTTP),
    (500, 20, None, Outcome.FAIL_HTTP),
    (302, 20, None, Outcome.FAIL_HTTP),       # redirects are not followed: health URL must answer 2xx
    (None, 5000, "timeout", Outcome.FAIL_TIMEOUT),
    (None, 3, "connection", Outcome.FAIL_CONNECTION),
])
def test_classify(status, ms, kind, expected):
    assert classify(status, ms, kind, slow_threshold_ms=1000) == expected


# ---------- next_state: helpers ----------
def run(outcomes, start=UNKNOWN, rules=RULES, flaky=False):
    """Feed a sequence of outcomes through the state machine; return every transition."""
    status, fails, succ, out = start, 0, 0, []
    for o in outcomes:
        t = next_state(status, fails, succ, o, flaky, rules)
        status, fails, succ = t.status, t.consecutive_failures, t.consecutive_successes
        out.append(t)
    return out


def statuses(ts):
    return [t.status for t in ts]


# ---------- single-step table (DESIGN.md section 5) ----------
@pytest.mark.parametrize("prev, fails, succ, outcome, flaky, expected_status, expected_reason", [
    (HEALTHY, 0, 5, OK, False, HEALTHY, None),
    (HEALTHY, 0, 5, SLOW, False, DEGRADED, "slow_response"),
    (HEALTHY, 0, 5, FAIL, False, DEGRADED, "http_error"),
    (HEALTHY, 0, 5, Outcome.FAIL_TIMEOUT, False, DEGRADED, "timeout"),
    (DEGRADED, 1, 0, FAIL, False, DOWN, "http_error"),
    (DEGRADED, 1, 0, OK, False, HEALTHY, None),           # success resets the failure counter
    (HEALTHY, 0, 3, OK, True, DEGRADED, "flaky"),
    (DOWN, 4, 0, FAIL, False, DOWN, "http_error"),
    (DOWN, 4, 0, OK, False, RECOVERING, "recovering"),
    (DOWN, 4, 0, OK, True, RECOVERING, "recovering"),     # flaky never overrides an outage
    (RECOVERING, 0, 1, FAIL, False, DOWN, "http_error"),  # D24
    (UNKNOWN, 0, 0, OK, False, HEALTHY, None),
])
def test_single_transitions(prev, fails, succ, outcome, flaky, expected_status, expected_reason):
    t = next_state(prev, fails, succ, outcome, flaky, RULES)
    assert (t.status, t.reason) == (expected_status, expected_reason)


# ---------- sequences ----------
def test_first_failure_is_degraded_without_incident():
    t = run([OK, FAIL])[-1]
    assert t.status == DEGRADED and not t.open_incident


def test_two_consecutive_failures_go_down_and_open_exactly_one_incident():
    ts = run([OK, FAIL, FAIL, FAIL, FAIL])
    assert statuses(ts) == [HEALTHY, DEGRADED, DOWN, DOWN, DOWN]
    assert [t.open_incident for t in ts] == [False, False, True, False, False]


def test_success_between_failures_resets_the_counter():
    assert DOWN not in statuses(run([FAIL, OK, FAIL, OK, FAIL]))


def test_recovery_needs_three_consecutive_successes():
    ts = run([FAIL, FAIL, OK, OK, OK])
    assert statuses(ts) == [DEGRADED, DOWN, RECOVERING, RECOVERING, HEALTHY]
    assert [t.resolve_incidents for t in ts] == [False, False, False, False, True]


def test_failure_during_recovery_returns_to_down_without_a_new_incident():
    ts = run([FAIL, FAIL, OK, OK, FAIL, OK, OK, OK])
    assert statuses(ts) == [DEGRADED, DOWN, RECOVERING, RECOVERING, DOWN, RECOVERING, RECOVERING, HEALTHY]
    assert sum(t.open_incident for t in ts) == 1
    assert sum(t.resolve_incidents for t in ts) == 1
    assert ts[4].recovery_interrupted


def test_slow_successes_count_towards_recovery_and_end_degraded():
    ts = run([FAIL, FAIL, SLOW, SLOW, SLOW])
    assert statuses(ts)[-1] == DEGRADED and ts[-1].resolve_incidents and ts[-1].reason == "slow_response"


def test_thresholds_come_from_configuration():
    ts = run([FAIL, FAIL, FAIL, OK], rules=Rules(failure_threshold=3, recovery_threshold=1))
    assert statuses(ts) == [DEGRADED, DEGRADED, DOWN, HEALTHY]
    assert ts[2].open_incident and ts[3].resolve_incidents


def test_failure_threshold_of_one_goes_straight_down():
    assert run([FAIL], rules=Rules(failure_threshold=1))[0].open_incident


def test_new_outage_after_full_recovery_opens_a_second_incident():
    ts = run([FAIL, FAIL, OK, OK, OK, FAIL, FAIL])
    assert sum(t.open_incident for t in ts) == 2


# ---------- flaky incidents (D49): independent second trigger, off by default ----------
from app.domain.state import OPEN_FLAKY, RESOLVE_FLAKY, flaky_incident_action  # noqa: E402


@pytest.mark.parametrize("is_flaky, has_open, opens, expected", [
    (True, False, False, None),          # default (PO): flaky = DEGRADED only, never an incident
    (True, False, True, OPEN_FLAKY),     # option on: open one
    (True, True, True, None),            # already open: no duplicate
    (False, True, True, RESOLVE_FLAKY),  # no longer flaky: resolve
    (False, True, False, RESOLVE_FLAKY), # option switched off later: still clean up the open one
    (False, False, True, None),
])
def test_flaky_incident_action(is_flaky, has_open, opens, expected):
    assert flaky_incident_action(is_flaky, has_open, opens) == expected
