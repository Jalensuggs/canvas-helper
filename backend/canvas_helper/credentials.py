import base64
import binascii
import os
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import EncryptedCredential, LOCAL_USER_ID
from .secret_store import SecretStore


class CredentialVault:
    """Mode-aware per-user credential storage."""

    def __init__(
        self,
        *,
        mode: str,
        desktop_store: SecretStore,
        encryption_key: str | None,
        key_version: int,
    ):
        self.mode = mode
        self.desktop_store = desktop_store
        self.key_version = key_version
        self._key = self._decode_key(encryption_key) if encryption_key else None

    @staticmethod
    def _decode_key(value: str) -> bytes:
        try:
            key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        except (ValueError, binascii.Error) as exc:
            raise ValueError("Credential encryption key must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("Credential encryption key must decode to 32 bytes")
        return key

    @staticmethod
    def _desktop_name(user_id: str, name: str) -> str:
        return f"{name}:{user_id}"

    async def get(self, session: AsyncSession, user_id: str, name: str) -> str | None:
        if self.mode == "local_desktop":
            value = self.desktop_store.get(self._desktop_name(user_id, name))
            if value is None and user_id == LOCAL_USER_ID:
                value = self.desktop_store.get(name)
            return value
        if self._key is None:
            raise HTTPException(
                status_code=503, detail="Server credential encryption is not configured"
            )
        row = await session.scalar(
            select(EncryptedCredential).where(
                EncryptedCredential.user_id == user_id,
                EncryptedCredential.name == name,
            )
        )
        if row is None:
            return None
        if row.key_version != self.key_version:
            raise HTTPException(
                status_code=503, detail="Credential encryption key version is unavailable"
            )
        aad = f"{user_id}:{name}:{row.key_version}".encode()
        try:
            return AESGCM(self._key).decrypt(row.nonce, row.ciphertext, aad).decode()
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="Stored credential could not be decrypted"
            ) from exc

    async def set(
        self, session: AsyncSession, user_id: str, name: str, value: str
    ) -> None:
        value = value.strip()
        if not value:
            raise ValueError("Credential cannot be empty")
        if self.mode == "local_desktop":
            scoped = self._desktop_name(user_id, name)
            self.desktop_store.set(scoped, value)
            if user_id == LOCAL_USER_ID:
                self.desktop_store.set(name, value)
            return
        if self._key is None:
            raise HTTPException(
                status_code=503, detail="Server credential encryption is not configured"
            )
        row = await session.scalar(
            select(EncryptedCredential).where(
                EncryptedCredential.user_id == user_id,
                EncryptedCredential.name == name,
            )
        )
        nonce = os.urandom(12)
        aad = f"{user_id}:{name}:{self.key_version}".encode()
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode(), aad)
        now = datetime.now(timezone.utc)
        if row is None:
            session.add(
                EncryptedCredential(
                    user_id=user_id,
                    name=name,
                    ciphertext=ciphertext,
                    nonce=nonce,
                    key_version=self.key_version,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.ciphertext = ciphertext
            row.nonce = nonce
            row.key_version = self.key_version
            row.updated_at = now
        await session.commit()
