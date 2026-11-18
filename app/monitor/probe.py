"""Performs one HTTP health check. Async, so a slow service never blocks the others."""
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.db import utcnow


@dataclass(frozen=True)
class ProbeResult:
    started_at: datetime
    completed_at: datetime
    http_status: int | None
    response_time_ms: int | None
    error_kind: str | None        # "http", "timeout", "connection" or None when 2xx
    error_message: str | None     # exact technical error, OPERATOR ONLY


async def probe(client: httpx.AsyncClient, url: str, timeout_ms: int) -> ProbeResult:
    started_at = utcnow()
    t0 = time.perf_counter()

    def elapsed_ms() -> int:
        return int((time.perf_counter() - t0) * 1000)

    try:
        # wait_for enforces a hard total limit; httpx's own timeout covers connect/read separately.
        timeout_s = timeout_ms / 1000
        resp = await asyncio.wait_for(client.get(url, timeout=timeout_s), timeout=timeout_s)
        ms = elapsed_ms()
        ok = 200 <= resp.status_code < 300
        return ProbeResult(started_at, utcnow(), resp.status_code, ms,
                           None if ok else "http",
                           None if ok else f"HTTP {resp.status_code}: {resp.text[:500]}")
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return ProbeResult(started_at, utcnow(), None, elapsed_ms(), "timeout",
                           f"No response within {timeout_ms} ms")
    except httpx.HTTPError as exc:
        return ProbeResult(started_at, utcnow(), None, elapsed_ms(), "connection",
                           f"{type(exc).__name__}: {exc}"[:500])
