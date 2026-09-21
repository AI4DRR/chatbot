"""Health endpoint — the only route implemented before the backend migration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_app_settings
from app.config import Settings
from app.db.connection import check_database
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    """Server status plus database connectivity.

    Sync handler on purpose: the database probe blocks, so FastAPI runs it in
    the threadpool instead of on the event loop.
    """
    db_ok = check_database(settings.database_url)
    return HealthResponse(
        status="ok",
        database="ok" if db_ok else "error",
        timestamp=datetime.now(UTC),
    )
