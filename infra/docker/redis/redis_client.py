

import os
import json
import logging
from typing import Any, Optional

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


REDIS_URL = os.getenv(
    "REDIS_URL",
    "redis://:abhi9955shek@localhost:6379/0",
)

class RedisClient:
    """
    Shared asynchronous Redis client.

    A single connection pool is reused instead of creating
    a new Redis connection for every request.
    """

    def __init__(self, redis_url: str = REDIS_URL):
        self.redis_url = redis_url
        self._client: Optional[Redis] = None

    async def connect(self) -> Redis:
        """
        Create Redis client if one does not already exist.
        """

        if self._client is None:
            self._client = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                encoding="utf-8",
                socket_connect_timeout=5,
                socket_timeout=5,
                health_check_interval=30,
            )

            try:
                await self._client.ping()
                logger.info("Redis connection established")

            except RedisError:
                logger.exception("Failed to connect to Redis")

                await self._client.aclose()
                self._client = None

                raise

        return self._client

    async def get_client(self) -> Redis:
        """
        Return the active Redis client.
        """

        if self._client is None:
            await self.connect()

        return self._client

    async def health_check(self) -> bool:
        """
        Check whether Redis is available.
        """

        try:
            client = await self.get_client()
            return bool(await client.ping())

        except RedisError:
            logger.exception("Redis health check failed")
            return False

    async def close(self) -> None:
        """
        Close Redis connections during application shutdown.
        """

        if self._client is not None:
            await self._client.aclose()
            self._client = None

            logger.info("Redis connection closed")

    async def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        """
        Store a value in Redis.

        ttl:
            Expiration time in seconds.
        """

        try:
            client = await self.get_client()

            if isinstance(value, (dict, list)):
                value = json.dumps(value)

            await client.set(
                key,
                value,
                ex=ttl,
            )

            return True

        except RedisError:
            logger.exception(
                "Failed to SET Redis key: %s",
                key,
            )

            return False


    async def get(
        self,
        key: str,
    ) -> Optional[str]:
        """
        Retrieve a value from Redis.
        """

        try:
            client = await self.get_client()

            return await client.get(key)

        except RedisError:
            logger.exception(
                "Failed to GET Redis key: %s",
                key,
            )

            return None


    async def get_json(
        self,
        key: str,
    ) -> Optional[Any]:
        """
        Retrieve JSON data from Redis.
        """

        value = await self.get(key)

        if value is None:
            return None

        try:
            return json.loads(value)

        except json.JSONDecodeError:
            logger.warning(
                "Redis value is not valid JSON: %s",
                key,
            )

            return None


    async def delete(
        self,
        key: str,
    ) -> bool:
        """
        Delete a Redis key.
        """

        try:
            client = await self.get_client()

            await client.delete(key)

            return True

        except RedisError:
            logger.exception(
                "Failed to DELETE Redis key: %s",
                key,
            )

            return False


    async def exists(
        self,
        key: str,
    ) -> bool:
        """
        Check whether a Redis key exists.
        """

        try:
            client = await self.get_client()

            return bool(await client.exists(key))

        except RedisError:
            logger.exception(
                "Failed to check Redis key: %s",
                key,
            )

            return False


    async def cache_set(
        self,
        key: str,
        field: str,
        value: Any,
        ttl: Optional[int] = None,
    ) -> bool:
        try:
            client = await self.get_client()
            await client.hset(key, field, json.dumps(value) if isinstance(value, (dict, list)) else value)
            if ttl:
                await client.expire(key, ttl)
            return True
        except RedisError:
            logger.exception("Failed to HSET Redis key: %s", key)
            return False

    async def cache_get(
        self,
        key: str,
        field: str,
    ) -> Optional[Any]:
        try:
            client = await self.get_client()
            value = await client.hget(key, field)
            if value is None:
                return None
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        except RedisError:
            logger.exception("Failed to HGET Redis key: %s", key)
            return None

    async def create_session(
        self,
        session_id: str,
        data: dict[str, Any],
        ttl: Optional[int] = None,
    ) -> bool:
        try:
            client = await self.get_client()
            await client.set(
                f"session:{session_id}",
                json.dumps(data),
                ex=ttl,
            )
            return True
        except RedisError:
            logger.exception("Failed to create session: %s", session_id)
            return False

    async def get_session(
        self,
        session_id: str,
    ) -> Optional[dict[str, Any]]:
        try:
            client = await self.get_client()
            value = await client.get(f"session:{session_id}")
            if value is None:
                return None
            return json.loads(value)
        except RedisError:
            logger.exception("Failed to get session: %s", session_id)
            return None

    async def delete_session(
        self,
        session_id: str,
    ) -> bool:
        return await self.delete(f"session:{session_id}")


# ============================================================
# Counter Operations
# Used for rate limits and spend limits
# ============================================================

    async def increment(
        self,
        key: str,
        amount: int = 1,
        ttl: Optional[int] = None,
    ) -> int:
        """
        Increment a Redis counter.

        Useful for:
        - API rate limiting
        - Request counters
        - Spend tracking
        """

        client = await self.get_client()

        value = await client.incrby(
            key,
            amount,
        )

        # Only apply expiration when the key
        # is first created.
        if ttl and value == amount:
            await client.expire(
                key,
                ttl,
            )

        return value

    async def increment_spend(
        self,
        wallet_address: str,
        amount: int,
        period: str,
        ttl: Optional[int] = None,
    ) -> int:
        key = f"spend:{period}:{wallet_address.lower()}"
        return await self.increment(key, amount, ttl)

    async def check_rate_limit(
        self,
        identifier: str,
        limit: int,
        window_seconds: int,
    ) -> dict[str, Any]:
        key = f"ratelimit:{identifier}"
        client = await self.get_client()
        current = await client.incr(key)
        if current == 1:
            await client.expire(key, window_seconds)
        allowed = current <= limit
        return {
            "allowed": allowed,
            "current": current,
            "limit": limit,
            "window_seconds": window_seconds,
        }


# ============================================================
# Distributed Locks
# ============================================================

    async def acquire_lock(
        self,
        lock_name: str,
        timeout: int = 30,
    ):
        """
        Create a Redis distributed lock.

        Example:
        NFT purchase lock
        """

        client = await self.get_client()

        lock = client.lock(
            name=f"lock:{lock_name}",
            timeout=timeout,
            blocking_timeout=5,
        )

        acquired = await lock.acquire()

        if not acquired:
            return None

        return lock


    async def release_lock(
        self,
        lock,
    ) -> None:
        """
        Release a Redis distributed lock.
        """

        if lock:

            try:
                await lock.release()

            except RedisError:
                logger.exception(
                    "Failed to release Redis lock"
                )

    async def acquire_nft_lock(
        self,
        chain_id: int,
        nft_contract: str,
        token_id: int,
        timeout: int = 30,
    ):
        lock_name = f"nft:{chain_id}:{nft_contract}:{token_id}"
        return await self.acquire_lock(lock_name, timeout)


redis_client = RedisClient()


async def get_redis() -> Redis:
    """
    Return shared Redis connection.

    Usage:

    redis = await get_redis()
    """

    return await redis_client.get_client()


async def check_redis_health() -> bool:
    """
    Used by /health or /ready endpoints.
    """

    return await redis_client.health_check()


async def close_redis() -> None:
    """
    Call during FastAPI shutdown.
    """

    await redis_client.close()