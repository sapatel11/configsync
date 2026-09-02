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
    """One immutable configuration version and its resolved artifact."""

    name: str
    version: int
    checksum: str
    content: dict[str, Any]
    created_at: datetime


class CustomerStateUpdate(BaseModel):
    """Actual state reported by a reconciliation agent."""

    applied_version: int = Field(ge=0)
    status: str = Field(min_length=1, max_length=32)
    error: str | None = Field(default=None, max_length=500)


class CustomerStateResponse(BaseModel):
    """Last actual state reported for one customer/configuration pair."""

    customer: str
    config_name: str
    applied_version: int
    status: str
    error: str | None
    updated_at: datetime


class RolloutResponse(BaseModel):
    """Result of one staged rollout attempt."""

    id: int
    config_name: str
    target_version: int
    previous_version: int
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime
