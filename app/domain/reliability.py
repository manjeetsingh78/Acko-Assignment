"""Availability and error-budget maths (D32). Pure functions, no I/O.

Availability is computed from *sampled* checks: each check stands for the time until the next
one, so outages shorter than the check interval can be missed. The covered time and the number
of checks are always returned so the UI can show what a percentage is based on.
"""
import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Availability:
    total_checks: int
    successful_checks: int
    covered_minutes: float      # from the first check in the window until now
    window_minutes: float
    sufficient: bool            # False -> show "insufficient data", never a made-up 100 %
    percent: float | None


@dataclass(frozen=True)
class ErrorBudget:
    allowed_minutes: float
    used_minutes: float
    remaining_minutes: float
    remaining_percent: float | None
    exhausted: bool


def availability(total_checks: int, successful_checks: int, covered_minutes: float,
                 window_minutes: float, min_checks: int = 10) -> Availability:
    sufficient = total_checks >= min_checks
    percent = round(successful_checks / total_checks * 100, 3) if total_checks else None
    return Availability(total_checks, successful_checks, round(covered_minutes, 2), window_minutes,
                        sufficient, percent)


def allowed_downtime_minutes(target_percent: float, minutes: float) -> float:
    """Example: 99.9 % over 24 h -> 1440 * 0.001 = 1.44 minutes."""
    return minutes * (1 - target_percent / 100)


def error_budget(target_percent: float, avail: Availability) -> ErrorBudget | None:
    """Budget over the covered part of the window. None when there is no data at all."""
    if avail.percent is None:
        return None
    allowed = allowed_downtime_minutes(target_percent, avail.covered_minutes)
    used = avail.covered_minutes * (1 - avail.percent / 100)
    remaining = allowed - used
    remaining_pct = round(remaining / allowed * 100, 1) if allowed > 0 else None
    return ErrorBudget(round(allowed, 3), round(used, 3), round(remaining, 3), remaining_pct, remaining < 0)


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile, p in 0..100. None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))  # nearest-rank, 1-based
    return ordered[rank - 1]
