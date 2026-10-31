"""Pydantic v2 request/response wire schemas for the connectors module."""
from datetime import datetime
from typing import Any
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class RegisterConnectorRequest(BaseModel):
    """Inbound request to attach an external connector account."""
    connector_id: str = Field(..., description="Connector identifier (e.g. 'gmail').")
    account_label: str = Field(..., description="User label (e.g. 'work-email@company.com').")
    raw_credentials: dict[str, Any] = Field(..., description="Raw OAuth tokens to be encrypted with AES-256-GCM.")
    scopes: list[str] = Field(default_factory=list, description="Granted OAuth scopes.")


class ConnectorAccountResponse(BaseModel):
    """Outbound representation of a registered connector account (excluding raw secrets)."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Account UUID.")
    user_id: UUID = Field(..., description="Owning user UUID.")
    connector_id: str = Field(..., description="Connector identifier.")
    account_label: str = Field(..., description="User label.")
    scopes: list[str] = Field(..., description="Configured scopes.")
    status: str = Field(..., description="Account health ('active', 'needs_reauth', 'error', 'disabled').")
    enabled_tools: list[str] = Field(default_factory=list, description="MCP tool names enabled for this connector.")


class SyncTriggerResponse(BaseModel):
    """Outbound confirmation that connector sync was dispatched."""
    account_id: UUID
    job_id: UUID
    status: str


class ItemResponse(BaseModel):
    """Outbound representation of an ingested connector item."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    connector_account_id: UUID
    external_id: str
    thread_id: str
    sender: str
    recipients: list[str]
    subject: str
    body_text: str
    received_at: datetime
    labels: list[str]
