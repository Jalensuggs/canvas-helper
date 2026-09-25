import json
import os
from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    def set(self, name: str, value: str) -> None: ...
    def get(self, name: str) -> str | None: ...
    def delete(self, name: str) -> None: ...


class RestrictedFileSecretStore:
    """Development-only fallback using a mode-0600 file."""

    def __init__(self, path: Path):
        self.path = path

    def _read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        mode = self.path.stat().st_mode & 0o777
        if mode & 0o077:
            raise PermissionError("Secret store permissions are too broad")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if temp.exists():
                temp.unlink()

    def set(self, name: str, value: str) -> None:
        if not value.strip():
            raise ValueError("Secret cannot be empty")
        data = self._read()
        data[name] = value.strip()
        self._write(data)

    def get(self, name: str) -> str | None:
        return self._read().get(name)

    def delete(self, name: str) -> None:
        data = self._read()
        data.pop(name, None)
        self._write(data)


class KeyringSecretStore:
    """Desktop credentials backed by the operating system credential vault."""

    def __init__(self, service: str):
        self.service = service

    def set(self, name: str, value: str) -> None:
        if not value.strip():
            raise ValueError("Secret cannot be empty")
        import keyring

        keyring.set_password(self.service, name, value.strip())

    def get(self, name: str) -> str | None:
        import keyring

        return keyring.get_password(self.service, name)

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import PasswordDeleteError

        try:
            keyring.delete_password(self.service, name)
        except PasswordDeleteError:
            pass


class MigratingKeyringSecretStore:
    """Read-once migration from the legacy restricted file into the keyring."""

    def __init__(self, keyring_store: KeyringSecretStore, legacy_path: Path):
        self.keyring_store = keyring_store
        self.legacy_store = RestrictedFileSecretStore(legacy_path)

    def set(self, name: str, value: str) -> None:
        self.keyring_store.set(name, value)

    def get(self, name: str) -> str | None:
        value = self.keyring_store.get(name)
        if value is not None:
            return value
        legacy = self.legacy_store.get(name)
        if legacy is not None:
            self.keyring_store.set(name, legacy)
            self.legacy_store.delete(name)
        return legacy

    def delete(self, name: str) -> None:
        self.keyring_store.delete(name)
        self.legacy_store.delete(name)
