import time
import asyncio
import logging
from contextlib import contextmanager
from enum import Enum

from exceptions import TransientDependencyError

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    _tracer = trace.get_tracer("agent-orchestrator.planning-pipeline")
except ImportError:
    _tracer = None  # OTel SDK not installed — spans become no-ops rather than a hard dependency


@contextmanager
def traced_span(name: str, **attributes):
    """No-op if the OpenTelemetry SDK isn't installed/configured — lets
    this file work today without a collector running, and start emitting
    real spans the moment one is wired up, with zero code changes here."""
    if _tracer is None:
        yield
        return

    with _tracer.start_as_current_span(name) as span:
        for key, value in attributes.items():
            span.set_attribute(key, str(value))
        yield span


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self):
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._state == CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()


async def with_retry(fn, *args, max_retries: int = 3, base_backoff_seconds: float = 1.0, circuit: CircuitBreaker | None = None, **kwargs):
    """Wraps any async call with exponential backoff. Pass a shared
    CircuitBreaker instance to also short-circuit after repeated failures
    rather than retrying against a dependency that's fully down."""
    if circuit and not circuit.allow():
        raise TransientDependencyError("Circuit breaker open — dependency likely degraded")

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            result = await fn(*args, **kwargs)
            if circuit:
                circuit.record_success()
            return result
        except Exception as exc:
            last_error = exc
            if circuit:
                circuit.record_failure()
            if attempt < max_retries:
                backoff = base_backoff_seconds * (2 ** (attempt - 1))
                logger.warning("Retry %d/%d after failure: %s (backing off %.1fs)", attempt, max_retries, exc, backoff)
                await asyncio.sleep(backoff)

    raise TransientDependencyError(f"Failed after {max_retries} attempts: {last_error}") from last_error


class TTLCache:
    """Simple in-process cache for read-heavy, short-lived data (price
    quotes, capability lookups within a single task). Swap for a Redis-
    backed cache if this needs to be shared across process instances —
    kept in-process here since planning-pipeline state is already
    per-task and short-lived."""

    def __init__(self, ttl_seconds: float = 30.0):
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: object) -> None:
        self._store[key] = (time.monotonic() + self.ttl_seconds, value)