"""Pricing transport schemas: one row per model, in integer micros per million tokens."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ModelPriceResponse(BaseModel):
    """A stored model price."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(description="Price row id.")
    model: str = Field(description="Model name the rate applies to.")
    prompt_micros_per_mtok: int = Field(description="Micros per million prompt tokens.")
    completion_micros_per_mtok: int = Field(
        description="Micros per million completion tokens."
    )
    currency: str = Field(description="ISO 4217 currency code.")
    updated_at: datetime = Field(description="Last change timestamp.")


class ModelPriceUpsert(BaseModel):
    """A price to set or replace."""

    prompt_micros_per_mtok: int = Field(
        ge=0, description="Micros per million prompt tokens."
    )
    completion_micros_per_mtok: int = Field(
        ge=0, description="Micros per million completion tokens."
    )
    currency: str = Field(default="USD", min_length=3, max_length=3)
