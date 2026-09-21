"""Schema for GET /health."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Same shape as the legacy ``HealthResponse``."""

    status: Literal["ok", "error"] = Field(description="API process status")
    database: Literal["ok", "error"] = Field(description="Database connectivity")
    timestamp: datetime
