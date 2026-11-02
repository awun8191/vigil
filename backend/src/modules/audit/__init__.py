"""Audit domain module."""
from src.modules.audit.data import AuditLogResponse, AuditPageResponse
from src.modules.audit.dependencies import get_audit_service
from src.modules.audit.repository import (
    AuditEntryCreateDTO,
    AuditEventDTO,
    AuditPageDTO,
    AuditRepository,
)
from src.modules.audit.router import build_audit_router
from src.modules.audit.service import (
    ACTION_APPROVAL_DECISION,
    ACTION_CONNECTOR_CONNECT,
    ACTION_SETTINGS_CHANGE,
    AuditService,
    record_event,
)

__all__ = [
    "ACTION_APPROVAL_DECISION",
    "ACTION_CONNECTOR_CONNECT",
    "ACTION_SETTINGS_CHANGE",
    "AuditEntryCreateDTO",
    "AuditEventDTO",
    "AuditLogResponse",
    "AuditPageDTO",
    "AuditPageResponse",
    "AuditRepository",
    "AuditService",
    "build_audit_router",
    "get_audit_service",
    "record_event",
]
