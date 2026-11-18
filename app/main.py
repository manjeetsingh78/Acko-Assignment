"""FastAPI entry point.

Startup: connect to Postgres -> create tables -> seed -> start the scheduler and notifier.
State lives in the database, so after a restart the scheduler simply picks up where it left off:
counters and open incidents are read back, and pending webhooks are retried.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.config import Settings, settings
from app.db import init_db, make_engine, make_session_factory, try_acquire_scheduler_lock, wait_for_db
from app.models import Service
from app.monitor.scheduler import Scheduler
from app.notifier import Notifier
from app.routes import api, public
from app.seed import seed_catalogue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)   # don't log every health-check request
log = logging.getLogger("pulse")


def create_app(s: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = make_engine(s.database_url)
        wait_for_db(engine)
        init_db(engine)
        app.state.engine = engine
        app.state.sessions = make_session_factory(engine)
        with app.state.sessions() as session:
            seeded = seed_catalogue(session, s) if s.seed_services else 0
        log.info("Pulse started: services_seeded=%d background=%s", seeded, s.run_background)

        background: list[asyncio.Task] = []
        client = httpx.AsyncClient(follow_redirects=False)
        scheduler = Scheduler(app.state.sessions, client, s.max_concurrent_checks, s.supervisor_interval_s)
        lock_conn = try_acquire_scheduler_lock(engine) if s.run_background else None
        if lock_conn is not None:
            app.state.scheduler_role = "active"
            notifier = Notifier(app.state.sessions, client, s.webhook_url, s.webhook_timeout_s, s.notifier_poll_s)
            background = [asyncio.create_task(scheduler.run_forever(), name="scheduler"),
                          asyncio.create_task(notifier.run_forever(), name="notifier")]
        elif s.run_background:
            # Another instance already checks the services: this one only serves the API (no double checks).
            app.state.scheduler_role = "standby"
            log.warning("scheduler lock held by another Pulse instance: running as API-only standby")
        else:
            app.state.scheduler_role = "disabled"
        yield
        for task in background:
            task.cancel()
        await asyncio.gather(*background, return_exceptions=True)
        await scheduler.stop()
        if lock_conn is not None:
            lock_conn.close()          # releases the advisory lock
        await client.aclose()
        engine.dispose()

    app = FastAPI(
        title="Pulse API",
        description=("Service health monitoring and incident management (backend only). "
                     "Public endpoints need no login. For operator endpoints, call `POST /api/auth/login` first; "
                     "the session cookie is then sent automatically (also from this page)."),
        lifespan=lifespan)
    app.state.settings = s
    # Signed, HttpOnly session cookie. SameSite=Lax blocks cross-site POSTs carrying the cookie.
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret, same_site="lax", max_age=8 * 3600)

    @app.get("/healthz", tags=["health"])
    @app.get("/api/health", tags=["health"])
    def healthz():
        """Pulse's own liveness check: is the app up and can it read its database?"""
        try:
            with app.state.sessions() as session:
                count = session.scalar(select(func.count()).select_from(Service))
        except Exception:  # noqa: BLE001 - any DB error means "not healthy"
            log.exception("health check could not read the database")
            return JSONResponse({"status": "error", "db": "unavailable"}, status_code=503)
        return {"status": "ok", "db": "ok", "services": count, "scheduler": app.state.scheduler_role}

    @app.get("/", include_in_schema=False)
    def root():
        """Backend-only product (D47): the interactive API docs are the operator's console."""
        return RedirectResponse("/docs")

    app.include_router(public.router)
    app.include_router(api.auth_router)
    app.include_router(api.router)
    return app


app = create_app()
