"""Tiered scheduler (D35): one independent asyncio loop per enabled service.

- Each service's loop is sequential, so checks for the SAME service can never overlap.
- Loops are independent and every request has a timeout, so a slow service never delays others.
- A semaphore bounds how many checks run at the same moment.
- The loop re-reads the service's config every cycle, so a PATCH takes effect on the next check.
- A supervisor starts loops for new/enabled services and stops loops for disabled ones.
- If a check finishes late, the next one runs immediately but missed checks are NOT replayed.
"""
import asyncio
import logging
import random
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import utcnow
from app.models import Service
from app.monitor.check_processor import process_check
from app.monitor.probe import probe

log = logging.getLogger("pulse.scheduler")


class Scheduler:
    def __init__(self, sessions: sessionmaker, client: httpx.AsyncClient, max_concurrent: int = 10,
                 supervisor_interval_s: float = 5, probe_fn=probe):
        self.sessions = sessions
        self.client = client
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.supervisor_interval_s = supervisor_interval_s
        self.probe_fn = probe_fn          # injectable, so tests can simulate slow services
        self.tasks: dict[int, asyncio.Task] = {}

    # ---- DB helpers (synchronous, run in a worker thread) ----
    def _enabled_ids(self) -> set[int]:
        with self.sessions() as s:
            return set(s.scalars(select(Service.id).where(Service.enabled.is_(True))))

    def _load_config(self, service_id: int):
        with self.sessions() as s:
            svc = s.get(Service, service_id)
            if svc is None or not svc.enabled:
                return None
            return svc.health_url, svc.timeout_ms, svc.check_interval_seconds

    # ---- supervisor ----
    async def run_forever(self) -> None:
        while True:
            try:
                await self.sync_tasks()
            except Exception:  # noqa: BLE001 - e.g. DB briefly unavailable; try again next round
                log.exception("supervisor could not sync monitor tasks")
            await asyncio.sleep(self.supervisor_interval_s)

    async def sync_tasks(self) -> None:
        wanted = await asyncio.to_thread(self._enabled_ids)
        for sid, task in list(self.tasks.items()):
            if sid not in wanted or task.done():
                task.cancel()
                del self.tasks[sid]
        for sid in wanted - self.tasks.keys():
            self.tasks[sid] = asyncio.create_task(self._monitor(sid), name=f"monitor-{sid}")

    async def stop(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    # ---- one service ----
    async def _monitor(self, service_id: int) -> None:
        loop = asyncio.get_running_loop()
        await asyncio.sleep(random.uniform(0, 1))   # spread the first checks after a restart
        next_due = loop.time()
        while True:
            config = await asyncio.to_thread(self._load_config, service_id)
            if config is None:
                return
            url, timeout_ms, interval_s = config

            # scheduled_at = when this check SHOULD have started; started_at - scheduled_at = lag
            lag_s = max(0.0, loop.time() - next_due)
            scheduled_at = utcnow() - timedelta(seconds=lag_s)
            try:
                async with self.semaphore:
                    result = await self.probe_fn(self.client, url, timeout_ms)
                await asyncio.to_thread(process_check, self.sessions, service_id, result, scheduled_at)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad cycle must not kill the loop
                log.exception("check cycle failed for service %s", service_id)

            next_due += interval_s
            if next_due < loop.time():      # we are late: don't replay missed checks in a burst
                next_due = loop.time()
            await asyncio.sleep(next_due - loop.time())
