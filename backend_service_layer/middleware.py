import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger("api_gateway.requests")

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        start = time.monotonic()
        logger.info("Request started", extra={"context": {"correlation_id": correlation_id, "method": request.method, "path": request.url.path}})
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception("Request failed", extra={"context": {"correlation_id": correlation_id, "method": request.method, "path": request.url.path, "duration_ms": round(duration_ms, 1)}})
            raise
        duration_ms = (time.monotonic() - start) * 1000
        logger.info("Request completed", extra={"context": {"correlation_id": correlation_id, "method": request.method, "path": request.url.path, "status_code": response.status_code, "duration_ms": round(duration_ms, 1)}})
        response.headers["X-Correlation-ID"] = correlation_id
        return response
