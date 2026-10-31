"""Auth domain module."""
from src.modules.auth.data import (
    AuthMeResponse,
    PairingRequest,
    PairingResponse,
    RevokeDeviceRequest,
    RevokeDeviceResponse,
)
from src.modules.auth.dependencies import (
    get_auth_service,
    get_current_device,
    get_current_user,
)
from src.modules.auth.repository import (
    AuthRepository,
    DeviceCreateDTO,
    DeviceDTO,
    PairingCodeDTO,
    UserDTO,
)
from src.modules.auth.router import build_auth_router
from src.modules.auth.service import AuthService

__all__ = [
    "AuthMeResponse",
    "AuthRepository",
    "AuthService",
    "DeviceCreateDTO",
    "DeviceDTO",
    "PairingCodeDTO",
    "PairingRequest",
    "PairingResponse",
    "RevokeDeviceRequest",
    "RevokeDeviceResponse",
    "UserDTO",
    "build_auth_router",
    "get_auth_service",
    "get_current_device",
    "get_current_user",
]
