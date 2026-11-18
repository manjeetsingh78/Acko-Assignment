"""The service state machine (DESIGN.md section 5). Pure function, no I/O, fully unit-tested.

Given the previous state, the counters and the outcome of the newest check, it returns the
new state, the new counters and which incident actions the caller must perform.
"""
from dataclasses import dataclass

from app.domain.classify import Outcome, is_success

HEALTHY, DEGRADED, DOWN, RECOVERING, UNKNOWN = "HEALTHY", "DEGRADED", "DOWN", "RECOVERING", "UNKNOWN"

FAILURE_REASON = {
    Outcome.FAIL_HTTP: "http_error",
    Outcome.FAIL_TIMEOUT: "timeout",
    Outcome.FAIL_CONNECTION: "connection_error",
}


@dataclass(frozen=True)
class Rules:
    failure_threshold: int = 2   # consecutive failures before DOWN (PO)
    recovery_threshold: int = 3  # consecutive successes before recovered (PO)


@dataclass(frozen=True)
class Transition:
    status: str
    reason: str | None
    consecutive_failures: int
    consecutive_successes: int
    open_incident: bool = False          # entered DOWN from a non-outage state
    resolve_incidents: bool = False      # recovery confirmed
    recovery_progress: bool = False      # a success while DOWN/RECOVERING, not yet confirmed
    recovery_interrupted: bool = False   # a failure while RECOVERING (D24)


def _available_state(outcome: Outcome, is_flaky: bool) -> tuple[str, str | None]:
    """State of a service that is answering successfully (not in an outage)."""
    if is_flaky:
        return DEGRADED, "flaky"
    if outcome == Outcome.OK_SLOW:
        return DEGRADED, "slow_response"
    return HEALTHY, None


def next_state(prev_status: str, consecutive_failures: int, consecutive_successes: int,
               outcome: Outcome, is_flaky: bool, rules: Rules) -> Transition:
    success = is_success(outcome)
    if success:
        fails, succ = 0, consecutive_successes + 1
    else:
        fails, succ = consecutive_failures + 1, 0

    in_outage = prev_status in (DOWN, RECOVERING)

    if in_outage:
        if not success:
            # Still down, or recovery interrupted: same outage, same incident, no new one (D24).
            return Transition(DOWN, FAILURE_REASON[outcome], fails, succ,
                              recovery_interrupted=(prev_status == RECOVERING))
        if succ >= rules.recovery_threshold:
            status, reason = _available_state(outcome, is_flaky)
            return Transition(status, reason, fails, succ, resolve_incidents=True)
        return Transition(RECOVERING, "recovering", fails, succ, recovery_progress=True)

    # Not in an outage: UNKNOWN, HEALTHY or DEGRADED.
    if not success:
        if fails >= rules.failure_threshold:
            return Transition(DOWN, FAILURE_REASON[outcome], fails, succ, open_incident=True)
        return Transition(DEGRADED, "flaky" if is_flaky else FAILURE_REASON[outcome], fails, succ)

    status, reason = _available_state(outcome, is_flaky)
    return Transition(status, reason, fails, succ)


# ---------------- flaky incidents (D49) ----------------
OPEN_FLAKY, RESOLVE_FLAKY = "open_flaky", "resolve_flaky"


def flaky_incident_action(is_flaky: bool, has_open_flaky_incident: bool, opens_incident: bool) -> str | None:
    """Separate from next_state(): flaky is a second, independent incident trigger.

    - open    when the flaky threshold is crossed, the option is on and no flaky incident is open yet
    - resolve when the service is no longer flaky (fewer than X failures in the window) and one is open
    The option is OFF by default: the PO's rule is "flaky = DEGRADED only" until clarified.
    """
    if is_flaky and opens_incident and not has_open_flaky_incident:
        return OPEN_FLAKY
    if not is_flaky and has_open_flaky_incident:
        return RESOLVE_FLAKY
    return None
