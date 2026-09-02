from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ConfigCreate(BaseModel):
    """Configuration content accepted when creating version 1."""

    content: dict[str, Any] = Field(min_length=1)


class ConfigUpdate(BaseModel):
    """Configuration content accepted when creating a new version."""

    content: dict[str, Any] = Field(min_length=1)


class ConfigResponse(BaseModel):
    """The current authoritative configuration version."""

    name: str
    version: int
    content: dict[str, Any]
    created_at: datetime
