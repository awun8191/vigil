"""Pydantic v2 request/response wire schemas for the auth module."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class PairingRequest(BaseModel):
    """Inbound request payload for exchanging one-time pairing code for bearer token."""
    pairing_code: str = Field(
        ...,
        min_length=6,
        max_length=32,
        description="Temporary one-time pairing code issued at server boot or settings.",
        examples=["VIG-789234"],
    )
    device_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Human-readable label identifying the client device.",
        examples=["Pixel 8 Pro - Alice"],
    )
    push_token: str | None = Field(
        default=None,
        description="Optional push notification device token (FCM, APNS, or ntfy topic).",
    )


class PairingResponse(BaseModel):
    """Outbound response containing the long-lived device bearer token (emitted once)."""
    model_config = ConfigDict(from_attributes=True)

    device_token: str = Field(
        ...,
        description="Secret raw bearer token for subsequent Authorization headers.",
        examples=["vgt_a1b2c3d4e5f6..."],
    )
    device_id: UUID = Field(
        ...,
        description="Persistent UUID assigned to the paired device.",
    )
    device_name: str = Field(
        ...,
        description="Device name assigned during pairing.",
        examples=["Pixel 8 Pro - Alice"],
    )
    user_id: UUID = Field(
        ...,
        description="Associated user UUID.",
    )


class AuthMeResponse(BaseModel):
    """Outbound response representing authenticated device identity."""
    model_config = ConfigDict(from_attributes=True)

    device_id: UUID = Field(..., description="Device unique identifier.")
    device_name: str = Field(..., description="Device label.")
    user_id: UUID = Field(..., description="User unique identifier.")
    user_display_name: str = Field(..., description="User display name.")
    created_at: datetime = Field(..., description="Device creation UTC timestamp.")
    last_seen_at: datetime = Field(..., description="Device last activity UTC timestamp.")


class RevokeDeviceRequest(BaseModel):
    """Inbound payload for revoking an active device token."""
    device_id: UUID = Field(..., description="UUID of the device to revoke.")


class RevokeDeviceResponse(BaseModel):
    """Outbound confirmation of device token revocation."""
    device_id: UUID = Field(..., description="Revoked device UUID.")
    revoked: bool = Field(default=True, description="Indicates successful revocation.")
    revoked_at: datetime = Field(..., description="Revocation UTC timestamp.")
