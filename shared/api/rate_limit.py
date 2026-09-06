import time
from collections import defaultdict, deque
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware


class InMemoryRateLimiter(BaseHTTPMiddleware):
    def __init__(self, app: Any, limit: int = 60, window_seconds: int = 60) -> None:
        super().__init__(app)
        self.limit = limit
        self.window_seconds = window_seconds
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        client_key = request.client.host if request.client else "unknown"
        now = time.time()
        bucket = self.requests[client_key]

        while bucket and now - bucket[0] > self.window_seconds:
            bucket.popleft()

        if len(bucket) >= self.limit:
            from fastapi.responses import JSONResponse
            from starlette import status

            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"error": {"code": "rate_limited", "message": "Too many requests"}},
            )

        bucket.append(now)
        return await call_next(request)
