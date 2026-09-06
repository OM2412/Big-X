import time
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class AbuseCheckResult:
    allowed: bool
    reason: str | None = None


class AbuseDetectionService:
    def __init__(self, redis_client, window_seconds: int = 60, max_requests_per_window: int = 20):
        self.redis = redis_client
        self.window_seconds = window_seconds
        self.max_requests_per_window = max_requests_per_window

    async def check(self, user_id: str, message: str) -> AbuseCheckResult:
        rate_result = await self._check_rate_limit(user_id)
        if not rate_result.allowed:
            return rate_result

        pattern_result = self._check_repeated_content(user_id, message)
        if not pattern_result.allowed:
            return pattern_result

        return AbuseCheckResult(allowed=True)

    async def _check_rate_limit(self, user_id: str) -> AbuseCheckResult:
        key = f"abuse:rate:{user_id}"
        now = int(time.time())
        window_start = now - self.window_seconds

        # Sliding window using a sorted set: score = timestamp, member = unique request id.
        await self.redis.zremrangebyscore(key, 0, window_start)
        request_count = await self.redis.zcard(key)

        if request_count >= self.max_requests_per_window:
            logger.warning("Rate limit exceeded for user %s: %d requests", user_id, request_count)
            return AbuseCheckResult(allowed=False, reason="Too many requests — please slow down")

        await self.redis.zadd(key, {f"{now}:{request_count}": now})
        await self.redis.expire(key, self.window_seconds)
        return AbuseCheckResult(allowed=True)

    def _check_repeated_content(self, user_id: str, message: str) -> AbuseCheckResult:
        # Simple heuristic: a message that's mostly one repeated character
        # or token is almost always spam/probing rather than a real request.
        stripped = message.strip()
        if len(stripped) > 20 and len(set(stripped)) <= 3:
            return AbuseCheckResult(allowed=False, reason="Message flagged as low-quality/repetitive content")
        return AbuseCheckResult(allowed=True)