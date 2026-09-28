"""API 金鑰加密（AES-256-GCM）與單人 API token 管理。"""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from goldhunter.config import get_settings


def _read_or_create(path: Path, factory) -> str:
    if path.exists():
        return path.read_text().strip()
    value = factory()
    path.write_text(value)
    os.chmod(path, 0o600)
    return value


def _key() -> bytes:
    s = get_settings()
    raw = s.secret_key or _read_or_create(
        s.data_dir / "secret.key", lambda: base64.b64encode(AESGCM.generate_key(256)).decode()
    )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise ValueError("GOLDHUNTER_SECRET_KEY 必須是 32 bytes 的 base64 字串")
    return key


def encrypt(plaintext: str | None) -> str | None:
    if not plaintext:
        return None
    nonce = os.urandom(12)
    ct = AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    blob = base64.b64decode(token)
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], None).decode()


def mask(value: str | None) -> str | None:
    """顯示用：只露出末 4 碼"""
    if not value:
        return None
    return "****" + value[-4:] if len(value) > 4 else "****"


def api_token() -> str:
    s = get_settings()
    return s.api_token or _read_or_create(s.data_dir / "api_token", lambda: secrets.token_urlsafe(32))
