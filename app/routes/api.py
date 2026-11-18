"""Operator JSON API. Every route here requires an operator session (D31), except login."""
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import incidents, reporting
from app.auth import check_credentials, current_user, require_operator
from app.catalog import ConfigError, create_service, update_service
from app.config import Settings
from app.models import Incident, Service
from app.routes.deps import get_db, get_settings
from app.schemas import (HealthCheckOut, IncidentDetail, IncidentOut, LoginIn, NoteIn, OperatorService,
                         ServiceCreate, ServiceUpdate)

auth_router = APIRouter(prefix="/api/auth", tags=["auth"])
router = APIRouter(prefix="/api", tags=["operator"], dependencies=[Depends(require_operator)])


# ---------------- auth ----------------
@auth_router.post("/login")
def login(body: LoginIn, request: Request, settings: Settings = Depends(get_settings)):
    if not check_credentials(body.username, body.password, settings):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    request.session["user"] = body.username
    return {"user": body.username}


@auth_router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@auth_router.get("/me")
def me(request: Request):
    user = current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {"user": user}


# ---------------- services ----------------
def _service_or_404(db: Session, service_id: int) -> Service:
    svc = db.get(Service, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    return svc


@router.get("/services", response_model=list[OperatorService])
def list_services(db: Session = Depends(get_db)):
    return list(db.scalars(select(Service).order_by(Service.name)))


@router.post("/services", response_model=OperatorService, status_code=201)
def add_service(body: ServiceCreate, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    try:
        svc = create_service(db, body, settings)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=e.errors)
    db.commit()
    return svc


@router.get("/services/{service_id}", response_model=OperatorService)
def get_service(service_id: int, db: Session = Depends(get_db)):
    return _service_or_404(db, service_id)


@router.patch("/services/{service_id}", response_model=OperatorService)
def patch_service(service_id: int, body: ServiceUpdate, db: Session = Depends(get_db)):
    svc = db.get(Service, service_id, with_for_update=True)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    try:
        update_service(db, svc, body)
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=e.errors)
    db.commit()
    return svc


@router.get("/services/{service_id}/history", response_model=list[HealthCheckOut])
@router.get("/services/{service_id}/checks", response_model=list[HealthCheckOut])
def service_checks(service_id: int, limit: int = Query(100, ge=1, le=1000), db: Session = Depends(get_db)):
    _service_or_404(db, service_id)
    return reporting.recent_checks(db, service_id, limit)


def _reliability_json(r: dict) -> dict:
    """Dataclasses -> plain dicts so they serialise to JSON."""
    budget = r["error_budget"]
    return {**r, "availability": asdict(r["availability"]), "error_budget": asdict(budget) if budget else None}


@router.get("/services/{service_id}/metrics")
def service_metrics(service_id: int,
                    window_hours: int | None = Query(None, ge=1, le=720,
                                                     description="Rolling window; default 24, use 168 for 7 days"),
                    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    svc = _service_or_404(db, service_id)
    return _reliability_json(reporting.service_reliability(db, svc, settings, window_hours=window_hours))


# ---------------- incidents ----------------
@router.get("/incidents", response_model=list[IncidentOut])
def get_incidents(status: str | None = Query(None, description="active | open | acknowledged | resolved"),
                  service_id: int | None = None, db: Session = Depends(get_db)):
    return reporting.list_incidents(db, status, service_id)


def _incident_or_404(db: Session, incident_id: int) -> Incident:
    inc = db.get(Incident, incident_id)
    if inc is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return inc


@router.get("/incidents/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: int, db: Session = Depends(get_db)):
    return reporting.incident_detail(db, _incident_or_404(db, incident_id))


def _incident_action(db: Session, action):
    try:
        action()
    except incidents.IncidentNotFound:
        raise HTTPException(status_code=404, detail="Incident not found")
    except incidents.IncidentAlreadyResolved:
        raise HTTPException(status_code=409, detail="Incident is already resolved")
    db.commit()


@router.post("/incidents/{incident_id}/acknowledge", response_model=IncidentDetail)
def acknowledge(incident_id: int, db: Session = Depends(get_db), user: str = Depends(require_operator)):
    _incident_action(db, lambda: incidents.acknowledge(db, incident_id, user))
    return reporting.incident_detail(db, _incident_or_404(db, incident_id))


@router.post("/incidents/{incident_id}/notes", response_model=IncidentDetail, status_code=201)
def add_note(incident_id: int, body: NoteIn, db: Session = Depends(get_db), user: str = Depends(require_operator)):
    _incident_action(db, lambda: incidents.add_note(db, incident_id, user, body.note))
    return reporting.incident_detail(db, _incident_or_404(db, incident_id))


# ---------------- dashboard + metrics ----------------
@router.get("/dashboard/summary")
def dashboard(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    """Everything the dashboard screen needs in one call (it polls every 5 s)."""
    services = []
    for svc in db.scalars(select(Service).order_by(Service.name)):
        recent = reporting.response_time_series(db, svc.id, 30)
        last = reporting.recent_checks(db, svc.id, 1)
        services.append({
            "service": OperatorService.model_validate(svc),
            "reliability": _reliability_json(reporting.service_reliability(db, svc, settings)),
            "reliability_7d": _reliability_json(reporting.service_reliability(db, svc, settings, window_hours=168)),
            "last_check": HealthCheckOut.model_validate(last[0]) if last else None,
            "recent": [{"at": at, "ms": ms, "ok": ok} for at, ms, ok in recent],
        })
    return {**reporting.dashboard_summary(db),
            "services": services,
            "active_incidents": reporting.list_incidents(db, "active"),
            "recent_incidents": reporting.list_incidents(db, limit=10)}


@router.get("/metrics/summary")
def metrics(db: Session = Depends(get_db)):
    return reporting.metrics_summary(db)
