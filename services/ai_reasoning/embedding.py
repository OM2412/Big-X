import asyncio
import hashlib
import logging
import os
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import httpx
import tenacity
from opentelemetry import trace
from pydantic_settings import BaseSettings
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

try:
    import cachetools
except ImportError:  # pragma: no cover
    cachetools = None

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None

from prometheus_client import Counter, Histogram

EmbeddingVector = List[float]
EmbeddingBatch = List[EmbeddingVector]


class EmbeddingError(Exception):
    """Base exception for embedding failures."""


class EmbeddingUnavailable(EmbeddingError):
    """Raised when the embedding service is unreachable or returns an error."""


class OpenAITimeout(EmbeddingError):
    """Raised when an embedding request times out."""


class InvalidEmbeddingRequest(EmbeddingError):
    """Raised when the input text is invalid."""


class EmbeddingBatchTooLarge(EmbeddingError):
    """Raised when the batch size exceeds the API limit."""


class Settings(BaseSettings):
    """Application settings for the embedding client.

    Values can be provided via environment variables with the ``EMBEDDING_``
    prefix, e.g. ``EMBEDDING_API_URL``, ``EMBEDDING_MODEL``.
    """

    model_config = {"env_prefix": "EMBEDDING_", "extra": "ignore"}

    provider: str = "openai"
    api_url: str = "https://api.openai.com/v1/embeddings"
    model: str = "text-embedding-3-small"
    api_key: Optional[str] = None
    timeout_connect: float = 5.0
    timeout_read: float = 30.0
    timeout_write: float = 10.0
    timeout_pool: float = 10.0
    max_batch_size: int = 100
    max_chars_per_chunk: int = 80000
    chunk_overlap: int = 200
    cache_enabled: bool = True
    cache_maxsize: int = 10_000
    cache_ttl: int = 3600


class Provider(str, Enum):
    OPENAI = "openai"
    AZURE_OPENAI = "azure_openai"
    OLLAMA = "ollama"
    SENTENCE_TRANSFORMERS = "sentence_transformers"
    VOYAGE = "voyage"
    COHERE = "cohere"


EMBEDDING_REQUESTS = Counter(
    "embedding_requests_total",
    "Total embedding API requests",
    ["provider", "model", "status"],
)
EMBEDDING_DURATION = Histogram(
    "embedding_duration_seconds",
    "Embedding request duration in seconds",
    ["provider", "model"],
)
EMBEDDING_FAILURES = Counter(
    "embedding_failures_total",
    "Total embedding failures",
    ["provider", "model", "error_type"],
)
EMBEDDING_CACHE_HITS = Counter(
    "embedding_cache_hits_total",
    "Number of embedding cache hits",
    ["provider", "model"],
)

tracer = trace.get_tracer(__name__)


def _should_retry(exc: BaseException) -> bool:
    """Determine whether an exception is retryable."""
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {429, 502, 503, 504}
    return False


class EmbeddingClient:
    """Production-ready embedding client with retries, caching, metrics, and tracing.

    Example::

        async with EmbeddingClient() as client:
            vec = await client.embed("Swap 1 ETH to USDC")
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self.settings = settings or Settings()
        self._owns_client = http_client is None

        if not self.settings.api_key:
            env_key = os.environ.get("OPENAI_API_KEY")
            if not env_key:
                raise InvalidEmbeddingRequest(
                    "API key required: set OPENAI_API_KEY or pass api_key to Settings"
                )
            self._api_key = env_key
        else:
            self._api_key = self.settings.api_key

        self._http = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=self.settings.timeout_connect,
                read=self.settings.timeout_read,
                write=self.settings.timeout_write,
                pool=self.settings.timeout_pool,
            )
        )

        self._logger = logging.getLogger(f"{__name__}.{self.settings.provider}")

        # Cache
        self._cache: Optional[Any] = None
        if self.settings.cache_enabled and cachetools is not None:
            self._cache = cachetools.LRUCache(maxsize=self.settings.cache_maxsize)

        # Tokenizer for token-aware chunking
        self._tokenizer = None
        if tiktoken is not None:
            try:
                self._tokenizer = tiktoken.encoding_for_model(self.settings.model)
            except KeyError:
                self._tokenizer = tiktoken.get_encoding("cl100k_base")

    async def __aenter__(self) -> "EmbeddingClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client and release resources."""
        if self._owns_client:
            await self._http.aclose()
            self._logger.info("HTTP client closed")

    def _validate_text(self, text: str) -> None:
        if text is None:
            raise InvalidEmbeddingRequest("Input text must not be None")
        if not isinstance(text, str):
            raise InvalidEmbeddingRequest(f"Input text must be str, got {type(text).__name__}")
        if not text.strip():
            raise InvalidEmbeddingRequest("Input text must not be empty or whitespace")

    def _chunk_text(self, text: str) -> List[str]:
        """Split long text into model-compatible chunks.

        Uses tiktoken when available for accurate token counts; falls back to
        a conservative character-based splitter otherwise.
        """
        self._validate_text(text)

        if not self._tokenizer:
            # Fallback: conservative character-based chunks
            max_chars = self.settings.max_chars_per_chunk
            if len(text) <= max_chars:
                return [text]

            chunks = []
            start = 0
            while start < len(text):
                end = min(start + max_chars, len(text))
                chunks.append(text[start:end])
                start = end - self.settings.chunk_overlap
                if start < 0:
                    start = 0
            return chunks

        tokens = self._tokenizer.encode(text)
        max_tokens = 8191  # text-embedding-3-small / ada-002 limit

        if len(tokens) <= max_tokens:
            return [text]

        chunk_size = max_tokens - self.settings.chunk_overlap
        if chunk_size <= 0:
            chunk_size = max_tokens

        chunks = []
        start = 0
        while start < len(tokens):
            end = min(start + chunk_size, len(tokens))
            chunk_tokens = tokens[start:end]
            chunks.append(self._tokenizer.decode(chunk_tokens))
            start += chunk_size
        return chunks

    def _cache_key(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_should_retry),
        reraise=True,
        before_sleep=lambda rs: logging.getLogger(__name__).warning(
            "Retrying embedding request after %s attempt(s)", rs.attempt_number
        ),
    )
    async def _post_with_retry(self, payload: Dict[str, Any], headers: Dict[str, str]) -> httpx.Response:
        """POST to the embedding API with exponential back-off."""
        resp = await self._http.post(
            self.settings.api_url,
            json=payload,
            headers=headers,
        )
        if resp.status_code in {429, 502, 503, 504}:
            self._logger.warning("Retryable status %s from embedding API", resp.status_code)
            resp.raise_for_status()
        return resp

    async def embed(self, text: str) -> EmbeddingVector:
        """Generate an embedding for a single text string.

        Args:
            text: Input text to embed.

        Returns:
            A single embedding vector.

        Raises:
            InvalidEmbeddingRequest: If text is empty, None, or whitespace.
            OpenAITimeout: If the request times out after retries.
            EmbeddingUnavailable: If the service returns a 5xx error.
            EmbeddingError: On other failures.
        """
        self._validate_text(text)

        if self._cache is not None:
            key = self._cache_key(text)
            cached = self._cache.get(key)
            if cached is not None:
                EMBEDDING_CACHE_HITS.labels(provider=self.settings.provider, model=self.settings.model).inc()
                return cached

        chunks = self._chunk_text(text)
        embeddings = await self.embed_batch(chunks)

        if len(embeddings) > 1:
            dim = len(embeddings[0])
            avg = [sum(v[i] for v in embeddings) / len(embeddings) for i in range(dim)]
            embeddings = [avg]

        result = embeddings[0]
        if self._cache is not None:
            self._cache[key] = result
        return result

    async def embed_batch(self, texts: List[str], batch_size: Optional[int] = None) -> EmbeddingBatch:
        """Generate embeddings for multiple texts in a single API call.

        Args:
            texts: List of texts to embed.
            batch_size: Optional override for max batch size.

        Returns:
            A list of embedding vectors in the same order as ``texts``.

        Raises:
            EmbeddingBatchTooLarge: If texts length exceeds batch size.
            InvalidEmbeddingRequest: If any input is empty.
            OpenAITimeout: If the request times out after retries.
            EmbeddingUnavailable: If the service returns an error.
        """
        if not texts:
            return []

        batch_size = batch_size or self.settings.max_batch_size
        if len(texts) > batch_size:
            raise EmbeddingBatchTooLarge(
                f"Batch size {len(texts)} exceeds configured limit {batch_size}"
            )

        for i, t in enumerate(texts):
            self._validate_text(t)

        headers = {"Authorization": f"Bearer {self._api_key}"}
        payload = {"model": self.settings.model, "input": texts}

        with EMBEDDING_DURATION.labels(provider=self.settings.provider, model=self.settings.model).time():
            with tracer.start_as_current_span("embedding.request") as span:
                span.set_attribute("provider", self.settings.provider)
                span.set_attribute("model", self.settings.model)
                span.set_attribute("batch_size", len(texts))
                span.set_attribute("input_characters", sum(len(t) for t in texts))

                start = time.time()
                try:
                    response = await self._post_with_retry(payload, headers)
                    status = response.status_code
                    EMBEDDING_REQUESTS.labels(
                        provider=self.settings.provider,
                        model=self.settings.model,
                        status=str(status),
                    ).inc()

                    if status == 401:
                        raise EmbeddingUnavailable("Invalid API key") from None
                    if status == 400:
                        raise InvalidEmbeddingRequest("Bad request to embedding API") from None
                    response.raise_for_status()

                    data = response.json()
                    sorted_data = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
                    embeddings = [d["embedding"] for d in sorted_data]

                    latency_ms = (time.time() - start) * 1000
                    self._logger.info(
                        "Embedding completed",
                        extra={
                            "model": self.settings.model,
                            "latency_ms": round(latency_ms, 1),
                            "batch_size": len(texts),
                            "input_characters": sum(len(t) for t in texts),
                            "status": status,
                        },
                    )
                    return embeddings

                except httpx.TimeoutException as exc:
                    EMBEDDING_FAILURES.labels(
                        provider=self.settings.provider,
                        model=self.settings.model,
                        error_type="timeout",
                    ).inc()
                    self._logger.error("Embedding timeout", extra={"model": self.settings.model})
                    raise OpenAITimeout("Embedding request timed out") from exc
                except httpx.HTTPStatusError as exc:
                    EMBEDDING_FAILURES.labels(
                        provider=self.settings.provider,
                        model=self.settings.model,
                        error_type="http_error",
                    ).inc()
                    self._logger.error(
                        "Embedding HTTP error",
                        extra={"status": exc.response.status_code, "error": str(exc)},
                    )
                    raise EmbeddingUnavailable(f"HTTP {exc.response.status_code}") from exc
                except EmbeddingError:
                    raise
                except Exception as exc:
                    EMBEDDING_FAILURES.labels(
                        provider=self.settings.provider,
                        model=self.settings.model,
                        error_type="unknown",
                    ).inc()
                    self._logger.exception("Embedding failed")
                    raise EmbeddingError(str(exc)) from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    async def demo() -> None:
        print("EmbeddingClient demo")
        print(f"Provider: {Provider.OPENAI}")
        print(f"Model: {Settings(model='text-embedding-3-small').model}")
        print("To run for real, set OPENAI_API_KEY and call:")
        print('  client = EmbeddingClient()')
        print('  vec = await client.embed("Swap 1 ETH to USDC")')
        print("Running a dry-run validation test...")

        client = EmbeddingClient(settings=Settings(api_key="test-key", cache_enabled=False))
        try:
            client._validate_text("")
        except InvalidEmbeddingRequest as e:
            print(f"Validation OK: {e}")

        try:
            await client.embed("   ")
        except InvalidEmbeddingRequest as e:
            print(f"Whitespace check OK: {e}")

        try:
            await client.embed_batch(["ok", None])  # type: ignore
        except InvalidEmbeddingRequest as e:
            print(f"Batch validation OK: {e}")

        try:
            await client.embed_batch(["a"] * 101)
        except EmbeddingBatchTooLarge as e:
            print(f"Batch size guard OK: {e}")

        await client.close()
        print("Dry-run complete.")

    asyncio.run(demo())
