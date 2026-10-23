"""Encrypted, machine-local secrets of trading accounts (broker passwords, API tokens, bridge tokens).

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) under a key kept in its own file next to the
database (``secret.key`` in the data folder, owner-only permissions where the OS supports them). A copy of
the database alone - shared for support, synced to a cloud drive - reveals no secret. Secrets never leave
the machine: no API returns them (only a short hint of the last characters) and backups leave them out.
"""

from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountCredential

KEY_FILE = "secret.key"
KINDS = ("password", "api_token", "webhook_secret", "bridge_token")
HINT_CHARS = 4
MAX_SECRET = 2000


class SecretError(Exception):
    """A stored secret cannot be read (the key file was replaced or removed)."""


class SecretBox:
    """Encrypts and decrypts with the key in ``<data_dir>/secret.key`` (created on first use)."""

    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / KEY_FILE
        self._fernet: Fernet | None = None
        self._lock = threading.Lock()

    def _key(self) -> Fernet:
        with self._lock:
            if self._fernet is None:
                if not self.path.exists():
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    key = Fernet.generate_key()
                    # Exclusive create: two processes starting together never overwrite each other's key.
                    try:
                        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    except FileExistsError:
                        pass
                    else:
                        with os.fdopen(fd, "wb") as handle:
                            handle.write(key)
                self._fernet = Fernet(self.path.read_bytes().strip())
            return self._fernet

    def encrypt(self, value: str) -> str:
        return self._key().encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._key().decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise SecretError("stored secret cannot be decrypted with this machine's key") from exc


def hint(value: str) -> str:
    return value[-HINT_CHARS:] if len(value) > HINT_CHARS * 2 else ""


def put(db: Session, box: SecretBox, account_id: int, kind: str, value: str) -> None:
    """Store (or with an empty ``value`` remove) one secret of an account. The caller commits."""
    row = db.scalar(
        select(AccountCredential).where(AccountCredential.account_id == account_id, AccountCredential.kind == kind)
    )
    if not value:
        if row is not None:
            db.delete(row)
        return
    if row is None:
        row = AccountCredential(account_id=account_id, kind=kind, ciphertext="")
        db.add(row)
    row.ciphertext = box.encrypt(value)
    row.hint = hint(value)


def get(db: Session, box: SecretBox, account_id: int, kind: str) -> str:
    """The secret in clear ("" when none is stored). Raises :class:`SecretError` if it cannot be read."""
    row = db.scalar(
        select(AccountCredential).where(AccountCredential.account_id == account_id, AccountCredential.kind == kind)
    )
    return box.decrypt(row.ciphertext) if row is not None else ""


def described(db: Session, account_id: int) -> dict[str, dict]:
    """``{kind: {"set": True, "hint": "a1b2"}}`` for the secrets an account has - safe to send to the browser."""
    rows = db.scalars(select(AccountCredential).where(AccountCredential.account_id == account_id))
    return {row.kind: {"set": True, "hint": row.hint} for row in rows}


def remove_account(db: Session, account_id: int) -> None:
    for row in db.scalars(select(AccountCredential).where(AccountCredential.account_id == account_id)):
        db.delete(row)


def new_bridge_token() -> str:
    return secrets.token_urlsafe(24)


def find_account_by_token(db: Session, box: SecretBox, token: str) -> int | None:
    """The account whose bridge token is ``token`` (constant-time comparison), or None."""
    if not token:
        return None
    found = None
    for row in db.scalars(select(AccountCredential).where(AccountCredential.kind == "bridge_token")):
        try:
            stored = box.decrypt(row.ciphertext)
        except SecretError:
            continue
        if secrets.compare_digest(stored.encode(), token.encode()):
            found = row.account_id
    return found
