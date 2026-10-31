"""Smoke tests verifying Milestone 1 Python environment, dependencies, and async test runner."""
import importlib
import io
import os
import sys
import pytest
import httpx
from fastapi import FastAPI
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import qrcode


def test_python_version_ge_312():
    """Verify runtime Python interpreter is >= 3.12."""
    assert sys.version_info >= (3, 12), f"Requires Python >= 3.12, got {sys.version}"


@pytest.mark.parametrize(
    "module_name",
    [
        "fastapi",
        "uvicorn",
        "pydantic_settings",
        "pydantic",
        "sqlalchemy",
        "asyncpg",
        "pgvector",
        "alembic",
        "httpx",
        "structlog",
        "cryptography",
        "qrcode",
        "pytest",
        "pytest_asyncio",
    ],
)
def test_core_dependencies_importable(module_name: str):
    """Verify all required Phase 1 core libraries are importable without errors."""
    mod = importlib.import_module(module_name)
    assert mod is not None


@pytest.mark.asyncio
async def test_asyncio_test_runner():
    """Verify pytest-asyncio auto mode correctly handles async coroutine tests."""
    import asyncio

    await asyncio.sleep(0.001)
    assert True


@pytest.mark.asyncio
async def test_fastapi_asgi_lifecycle():
    """Verify FastAPI application can instantiate and handle in-memory ASGI requests."""
    app = FastAPI(title="Virgil Smoke App")

    @app.get("/smoke")
    async def smoke():
        return {"status": "ok", "milestone": 1}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/smoke")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "milestone": 1}


def test_cryptography_aes_gcm():
    """Verify AES-256-GCM cipher works as expected for future F07 security primitives."""
    key = AESGCM.generate_key(bit_length=256)
    aes = AESGCM(key)
    nonce = os.urandom(12)
    secret = b"virgil-credentials"
    encrypted = aes.encrypt(nonce, secret, None)
    decrypted = aes.decrypt(nonce, encrypted, None)
    assert decrypted == secret


def test_qrcode_ascii_generation():
    """Verify ASCII QR code generation works without PIL."""
    qr = qrcode.QRCode()
    qr.add_data("VGL-TEST-1234")
    out = io.StringIO()
    qr.print_ascii(out=out)
    result = out.getvalue()
    assert len(result) > 50
