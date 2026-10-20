"""core/gemini_session_sync.py - Ma hoa & dong bo phien Gemini Web an toan giua may chu va app.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

LOGGER = logging.getLogger(__name__)

# Master key de ma hoa cookie phien lam viec (bao ve khoi bot scan tren GitHub)
_SECRET_SALT = b"KIEM_DUYET_BAO_VOV2_GEMINI_SESSION_TOKEN_SYNC_2711_2706"
_FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(_SECRET_SALT).digest())

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENC_PATH = BASE_DIR / "config" / "gemini_session.enc"


def encrypt_session_data(data: Dict[str, Any]) -> bytes:
    """Ma hoa dict session thanh bytes du lieu an toan bang Fernet AES."""
    from cryptography.fernet import Fernet
    f = Fernet(_FERNET_KEY)
    raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return f.encrypt(raw)


def decrypt_session_data(token: bytes) -> Optional[Dict[str, Any]]:
    """Giai ma token bytes thanh dict session."""
    from cryptography.fernet import Fernet
    try:
        f = Fernet(_FERNET_KEY)
        raw = f.decrypt(token)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:
        LOGGER.warning("Khong giai ma duoc gemini_session.enc: %s", exc)
        return None


def save_encrypted_session(data: Dict[str, Any], enc_path: Optional[Path] = None) -> bool:
    """Luu cau hinh phien da ma hoa vao config/gemini_session.enc."""
    target = enc_path or DEFAULT_ENC_PATH
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        token = encrypt_session_data(data)
        target.write_bytes(token)
        return True
    except Exception as exc:
        LOGGER.error("Khong ghi duoc enc session: %s", exc)
        return False


def load_encrypted_session(enc_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Doc va giai ma cau hinh phien tu config/gemini_session.enc."""
    target = enc_path or DEFAULT_ENC_PATH
    if not target.is_file():
        return None
    try:
        token = target.read_bytes()
        return decrypt_session_data(token)
    except Exception as exc:
        LOGGER.warning("Khong doc duoc file enc session: %s", exc)
        return None
