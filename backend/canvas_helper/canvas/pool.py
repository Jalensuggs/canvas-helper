"""Bounded, credential-aware cache of per-user Canvas clients.

Each :class:`CanvasClient` owns an HTTP connection pool, so one is kept per
user rather than per request. Two things make a plain dict wrong here:

* it grows with the number of users that have ever been served, and
* it keeps serving a client built from a token the user has since replaced,
  which matters because the API and worker are separate processes and only one
  of them sees the update.

Entries are therefore keyed by a digest of the credentials they were built
from, and the least recently used client is evicted past ``max_clients``.
"""

from collections import OrderedDict
import hashlib

import httpx

from .client import CanvasClient


def credential_digest(base_url: str, token: str) -> str:
    return hashlib.sha256(f"{base_url}\0{token}".encode()).hexdigest()


class CanvasClientPool:
    def __init__(
        self,
        *,
        max_clients: int = 128,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.max_clients = max_clients
        self.transport = transport
        self._entries: OrderedDict[str, tuple[str, CanvasClient]] = OrderedDict()

    def __contains__(self, user_id: str) -> bool:
        return user_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    async def get(self, user_id: str, base_url: str, token: str) -> CanvasClient:
        digest = credential_digest(base_url, token)
        existing = self._entries.get(user_id)
        if existing is not None:
            cached_digest, client = existing
            if cached_digest == digest:
                self._entries.move_to_end(user_id)
                return client
            # The credentials changed underneath us; the old client is useless.
            await self.discard(user_id)
        client = CanvasClient(base_url, token, transport=self.transport)
        self._entries[user_id] = (digest, client)
        self._entries.move_to_end(user_id)
        await self._evict()
        return client

    async def _evict(self) -> None:
        while len(self._entries) > self.max_clients:
            _, (_, client) = self._entries.popitem(last=False)
            await client.close()

    async def discard(self, user_id: str) -> None:
        entry = self._entries.pop(user_id, None)
        if entry is not None:
            await entry[1].close()

    async def close(self) -> None:
        entries, self._entries = self._entries, OrderedDict()
        for _, client in entries.values():
            await client.close()
