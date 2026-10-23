"""Web Push message encryption (RFC 8291) and VAPID authentication (RFC 8292).

Implemented on the ``cryptography`` package, so no extra web-push library is
needed. This module only builds requests; ``services.notify_channels`` sends them.

* VAPID: one P-256 key pair per journal, generated on first use. The private key
  is stored as the raw 32-byte scalar (base64url) and the public key as the
  65-byte uncompressed point (base64url) - the form browsers expect as
  ``applicationServerKey``.
* Payloads are encrypted with ``Content-Encoding: aes128gcm`` as a single record.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from urllib.parse import urlsplit

from .i18n import N_, tr

try:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    AVAILABLE = True
except ImportError:  # pragma: no cover - cryptography is in requirements.txt
    AVAILABLE = False

RECORD_SIZE = 4096
JWT_LIFETIME_SEC = 12 * 3600
DEFAULT_TTL_SEC = 3600
# Push services accept at most 4096 bytes of encrypted body; keep payloads well below.
MAX_PAYLOAD_BYTES = 3000
UNAVAILABLE_MESSAGE = N_("Web Push needs the 'cryptography' package: run pip install -r requirements.txt")


class WebPushError(ValueError):
    """Invalid subscription data or keys."""


def _require() -> None:
    if not AVAILABLE:
        raise WebPushError(tr(UNAVAILABLE_MESSAGE))


# ------------------------------------------------------------------- encoding


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    text = text.strip().replace("+", "-").replace("/", "_").rstrip("=")
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError) as exc:
        raise WebPushError(tr("Not valid base64url data")) from exc


def _public_bytes(key) -> bytes:
    return key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def load_public_key(raw: bytes):
    """Parse a 65-byte uncompressed P-256 point (raises WebPushError when it is not one)."""
    _require()
    if len(raw) != 65 or raw[0] != 0x04:
        raise WebPushError(tr("The p256dh key must be a 65-byte uncompressed P-256 point"))
    try:
        return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    except ValueError as exc:
        raise WebPushError(tr("The p256dh key is not a valid P-256 point")) from exc


def load_private_key(private_b64: str):
    """Rebuild the VAPID private key from its base64url raw scalar."""
    _require()
    raw = b64url_decode(private_b64)
    if len(raw) != 32:
        raise WebPushError(tr("The VAPID private key is invalid"))
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


# ---------------------------------------------------------------------- VAPID


def generate_vapid_keys() -> tuple[str, str]:
    """Return a new ``(private_key, public_key)`` pair, both base64url."""
    _require()
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return b64url_encode(private), b64url_encode(_public_bytes(key))


def audience(endpoint: str) -> str:
    """The JWT ``aud`` claim: scheme and host of the push service."""
    parts = urlsplit(endpoint)
    return f"{parts.scheme}://{parts.netloc}"


def vapid_jwt(endpoint: str, private_b64: str, subject: str, *, now: float | None = None) -> str:
    """Signed ES256 JWT for ``endpoint`` (RFC 8292), valid for 12 hours."""
    key = load_private_key(private_b64)
    issued = int(now if now is not None else time.time())
    header = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {"aud": audience(endpoint), "exp": issued + JWT_LIFETIME_SEC, "sub": subject}
    payload = b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode("ascii")
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{payload}.{b64url_encode(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def vapid_authorization(endpoint: str, private_b64: str, public_b64: str, subject: str) -> str:
    return f"vapid t={vapid_jwt(endpoint, private_b64, subject)}, k={public_b64}"


# ----------------------------------------------------------------- encryption


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    server_key=None,
    record_size: int = RECORD_SIZE,
) -> bytes:
    """Encrypt ``payload`` for one subscription (RFC 8291, aes128gcm, single record).

    ``salt`` and the ephemeral ``server_key`` are random unless given (tests).
    Returns ``salt || rs || idlen || keyid || ciphertext``.
    """
    _require()
    ua_public = b64url_decode(p256dh)
    auth_secret = b64url_decode(auth)
    if len(auth_secret) != 16:
        raise WebPushError(tr("The auth secret must be 16 bytes"))
    if len(payload) + 17 > record_size:
        raise WebPushError(tr("Payload too large for a single record"))
    salt = salt if salt is not None else os.urandom(16)
    server_key = server_key if server_key is not None else ec.generate_private_key(ec.SECP256R1())
    as_public = _public_bytes(server_key)

    shared = server_key.exchange(ec.ECDH(), load_public_key(ua_public))
    ikm = _hkdf(auth_secret, shared, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", record_size, len(as_public)) + as_public
    return header + ciphertext


def subscription_keys_valid(p256dh: str, auth: str) -> None:
    """Raise WebPushError unless the browser's subscription keys are usable."""
    load_public_key(b64url_decode(p256dh))
    if len(b64url_decode(auth)) != 16:
        raise WebPushError(tr("The auth secret must be 16 bytes"))
