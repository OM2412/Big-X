import json
import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

IPFS_GATEWAY = "https://ipfs.io/ipfs/"
ARWEAVE_GATEWAY = "https://arweave.net/"
CACHE_TTL_SECONDS = 300


@dataclass
class CacheEntry:
    data: dict
    fetched_at: float


class MetadataResolver:
    def __init__(self):
        self._cache: dict[str, CacheEntry] = {}
        self._http = httpx.AsyncClient(timeout=10.0)

    async def resolve(self, metadata_uri: str) -> dict:
        cached = self._cache.get(metadata_uri)
        if cached and (time.time() - cached.fetched_at) < CACHE_TTL_SECONDS:
            return cached.data

        url = self._to_http_url(metadata_uri)

        try:
            response = await self._http.get(url)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            logger.exception("Failed to resolve metadata: %s", metadata_uri)
            raise

        self._cache[metadata_uri] = CacheEntry(data=data, fetched_at=time.time())
        return data

    def _to_http_url(self, metadata_uri: str) -> str:
        if metadata_uri.startswith("ipfs://"):
            return IPFS_GATEWAY + metadata_uri.removeprefix("ipfs://")
        if metadata_uri.startswith("ar://"):
            return ARWEAVE_GATEWAY + metadata_uri.removeprefix("ar://")
        if metadata_uri.startswith("http://") or metadata_uri.startswith("https://"):
            return metadata_uri
        raise ValueError(f"Unrecognized metadata URI scheme: {metadata_uri}")

    async def close(self):
        await self._http.aclose()