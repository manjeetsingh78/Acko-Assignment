"""FastAPI entry point: opens the database, runs migrations and seeding on startup, exposes /healthz."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import Settings, settings
from app.db import connect, migrate
from app.seed import seed_catalogue

log = logging.getLogger("pulse")


def create_app(s: Settings = settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        conn = connect(s.db_path)
        applied = migrate(conn)
        seeded = seed_catalogue(conn, s) if s.seed_services else 0
        log.warning("Pulse started: db=%s migrations_applied=%s services_seeded=%d", s.db_path, applied, seeded)
        app.state.db = conn
        yield
        conn.close()

    app = FastAPI(title="Pulse", lifespan=lifespan)

    @app.get("/healthz")
    def healthz():
        """Pulse's own liveness check: is the app up and can it read its database?"""
        try:
            count = app.state.db.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        except Exception:
            return JSONResponse({"status": "error", "db": "unavailable"}, status_code=503)
        return {"status": "ok", "db": "ok", "services": count}

    return app


app = create_app()
