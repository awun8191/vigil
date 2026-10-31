"""Pydantic v2 request/response wire schemas for the chat module."""
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class CreateChatSessionRequest(BaseModel):
    """Inbound request to start a new chat session."""
    title: str | None = Field(default=None, max_length=150, description="Optional session title.")


class ChatSessionResponse(BaseModel):
    """Outbound representation of an interactive chat session."""
    model_config = ConfigDict(from_attributes=True)

    session_id: UUID = Field(..., description="Chat session UUID.")
    user_id: UUID = Field(..., description="Owning user UUID.")
    title: str | None = Field(default=None, description="Session title.")
    created_at: datetime = Field(..., description="Creation UTC timestamp.")
    updated_at: datetime = Field(..., description="Last message timestamp.")
    message_count: int = Field(default=0, description="Total turns in session.")


class SendChatMessageRequest(BaseModel):
    """Inbound message send payload."""
    content: str = Field(..., min_length=1, max_length=20000, description="User message text.")


class ChatMessageResponse(BaseModel):
    """Outbound wire representation of a chat message."""
    model_config = ConfigDict(from_attributes=True)

    id: UUID = Field(..., description="Message UUID.")
    session_id: UUID = Field(..., description="Parent session UUID.")
    role: str = Field(..., description="Message role ('user', 'assistant', 'system').")
    content: str = Field(..., description="Message text content.")
    created_at: datetime = Field(..., description="Creation timestamp.")
