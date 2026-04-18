"""
Helpers for encrypting and decrypting integration credentials.
"""

import base64
import binascii
import json
import re

from cryptography.fernet import Fernet, InvalidToken

from config import Config


def _normalize_encryption_key(raw: str) -> bytes:
    """
    Return bytes suitable for Fernet(key).

    Accepts:
    - A standard Fernet key from ``Fernet.generate_key().decode()`` (url-safe base64, 32 bytes).
    - A 64-char hex string (32 raw bytes), common copy-paste mistake.
    """
    key = (raw or "").strip()
    if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
        key = key[1:-1].strip()
    # Accidental copy of Python repr: b'...'
    if key.startswith("b'") and key.endswith("'"):
        key = key[2:-1].strip()
    if key.startswith('b"') and key.endswith('"'):
        key = key[2:-1].strip()

    if not key:
        raise ValueError("CREDENTIAL_ENCRYPTION_KEY is empty")

    try:
        Fernet(key.encode("utf-8"))
        return key.encode("utf-8")
    except Exception:
        pass

    hex_candidate = re.sub(r"\s+", "", key)
    if len(hex_candidate) == 64 and re.fullmatch(r"[0-9A-Fa-f]+", hex_candidate):
        try:
            raw32 = binascii.unhexlify(hex_candidate)
            encoded = base64.urlsafe_b64encode(raw32)
            Fernet(encoded)
            return encoded
        except (binascii.Error, ValueError):
            pass

    raise ValueError(
        "Invalid CREDENTIAL_ENCRYPTION_KEY. Generate a Fernet key and set it in .env, e.g.\n"
        '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"\n'
        "Use the single-line output as CREDENTIAL_ENCRYPTION_KEY=... (no quotes needed). "
        "If the key ends with '=' wrap the value in double quotes in .env."
    )


def _get_fernet() -> Fernet:
    raw = Config.CREDENTIAL_ENCRYPTION_KEY
    if not (raw or "").strip():
        raise ValueError(
            "CREDENTIAL_ENCRYPTION_KEY is not configured. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(_normalize_encryption_key(raw))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(
            "Invalid CREDENTIAL_ENCRYPTION_KEY format. Expected a Fernet key "
            "(from Fernet.generate_key()) or 64 hex characters (32 bytes)."
        ) from exc


def encrypt_text(value: str) -> str:
    if value is None:
        raise ValueError("Cannot encrypt None value")
    token = _get_fernet().encrypt(value.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_text(value: str) -> str:
    if not value:
        raise ValueError("Encrypted value is required")
    try:
        decoded = _get_fernet().decrypt(value.encode("utf-8"))
        return decoded.decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt credentials: invalid token or key") from exc


def encrypt_json(data: dict) -> str:
    return encrypt_text(json.dumps(data))


def decrypt_json(value: str) -> dict:
    return json.loads(decrypt_text(value))
