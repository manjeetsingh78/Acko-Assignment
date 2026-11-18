"""Public, read-only API for customers. No login; returns ONLY the customer-safe schemas (D30)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import reporting
from app.routes.deps import get_db
from app.schemas import PublicHistory, PublicStatus

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/status", response_model=PublicStatus)
def status(db: Session = Depends(get_db)):
    return reporting.public_status(db)


@router.get("/history", response_model=PublicHistory)
def history(days: int = Query(7, ge=1, le=30), db: Session = Depends(get_db)):
    return reporting.public_history(db, days)
