"""Turns one raw health-check response into one of five outcomes (D22). Pure function, no I/O."""
from enum import Enum


class Outcome(str, Enum):
    OK_FAST = "OK_FAST"                # 2xx within the slow threshold
    OK_SLOW = "OK_SLOW"                # 2xx, but slower than the slow threshold
    FAIL_HTTP = "FAIL_HTTP"            # answered, but not 2xx (e.g. 503, 500, 302)
    FAIL_TIMEOUT = "FAIL_TIMEOUT"      # no answer within the timeout
    FAIL_CONNECTION = "FAIL_CONNECTION"  # could not connect at all (DNS, refused, reset)


SUCCESS_OUTCOMES = {Outcome.OK_FAST, Outcome.OK_SLOW}


def is_success(outcome: Outcome) -> bool:
    return outcome in SUCCESS_OUTCOMES


def classify(http_status: int | None, response_time_ms: int | None, error_kind: str | None,
             slow_threshold_ms: int) -> Outcome:
    """error_kind is set by the probe: "timeout", "connection", or None when we got an HTTP answer."""
    if error_kind == "timeout":
        return Outcome.FAIL_TIMEOUT
    if error_kind == "connection":
        return Outcome.FAIL_CONNECTION
    if http_status is None or not 200 <= http_status < 300:
        return Outcome.FAIL_HTTP
    if response_time_ms is not None and response_time_ms > slow_threshold_ms:
        return Outcome.OK_SLOW
    return Outcome.OK_FAST
