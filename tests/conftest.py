"""Shared test setup.

The desktop credential path goes through the operating system keyring. On a
headless machine — any CI runner, any container — there is no backend, so
`keyring` either raises `NoKeyringError` or blocks trying to reach a secret
service. An in-memory backend keeps the real `KeyringSecretStore` code under
test while making the suite hermetic.
"""

import keyring
import keyring.backend
import pytest


class InMemoryKeyring(keyring.backend.KeyringBackend):
    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        super().__init__()
        self._values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._values.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._values[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._values.pop((service, username), None)


@pytest.fixture(autouse=True)
def in_memory_keyring():
    previous = keyring.get_keyring()
    backend = InMemoryKeyring()
    keyring.set_keyring(backend)
    try:
        yield backend
    finally:
        keyring.set_keyring(previous)
