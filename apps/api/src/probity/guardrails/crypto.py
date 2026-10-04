"""Field-level protection for bank accounts / PAN (Security.md §4).

Store last4 + HMAC (for matching and diffing without decrypting) + AES-GCM ciphertext.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from probity.config import get_settings


def normalize_account(acct: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", acct or "").upper()


def account_hmac(acct: str) -> str:
    key = get_settings().hmac_key.encode()
    return hmac.new(key, normalize_account(acct).encode(), hashlib.sha256).hexdigest()


def last4(acct: str) -> str:
    return normalize_account(acct)[-4:]


def mask(last_four: str | None) -> str:
    return f"XXXX{last_four}" if last_four else "—"


def _key() -> bytes:
    raw = base64.b64decode(get_settings().field_key_b64)
    return hashlib.sha256(raw).digest()  # always 32 bytes


def encrypt(plaintext: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key()).encrypt(nonce, plaintext.encode(), None)


def decrypt(blob: bytes) -> str:
    return AESGCM(_key()).decrypt(blob[:12], blob[12:], None).decode()
