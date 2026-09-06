
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import text as sql_text

from .embedding import EmbeddingClient

logger = logging.getLogger(__name__)

CHUNK_SIZE_CHARS = 1500
CHUNK_OVERLAP_CHARS = 200


@dataclass
class RetrievalResult:
    content: str
    score: float
    source: str


class KnowledgeBase:
    def __init__(self, embedding_client: EmbeddingClient, db_session_factory):
        self.embedding_client = embedding_client
        self.db_session_factory = db_session_factory

    async def ingest_document(self, source: str, text: str) -> int:
        """Chunks a document and stores embedded chunks. Call this once
        per doc (protocol whitepaper, incident postmortem, risk policy
        writeup) — not part of the request-time path."""
        chunks = self._chunk_text(text)
        embeddings = await self.embedding_client.embed_batch(chunks)

        async with self.db_session_factory() as session:
            for chunk, embedding in zip(chunks, embeddings):
                await session.execute(
                    sql_text(
                        "INSERT INTO knowledge_chunks (id, source, content, embedding) "
                        "VALUES (:id, :source, :content, :embedding)"
                    ),
                    {"id": str(uuid.uuid4()), "source": source, "content": chunk, "embedding": str(embedding)},
                )
            await session.commit()

        logger.info("Ingested %d chunks from %s", len(chunks), source)
        return len(chunks)

    async def embed(self, query: str) -> list[float]:
        """Satisfies the vector_store.embed() interface MemoryKnowledgeAgent expects."""
        return await self.embedding_client.embed(query)

    async def query(self, embedding: list[float], top_k: int = 5) -> list[RetrievalResult]:
        """Satisfies the vector_store.query() interface. Uses pgvector's
        cosine distance operator (<=>) — requires `CREATE EXTENSION
        vector;` and a `knowledge_chunks` table with an `embedding
        vector(1536)` column to exist already."""
        async with self.db_session_factory() as session:
            result = await session.execute(
                sql_text(
                    "SELECT content, source, 1 - (embedding <=> :query_embedding) AS similarity "
                    "FROM knowledge_chunks ORDER BY embedding <=> :query_embedding LIMIT :top_k"
                ),
                {"query_embedding": str(embedding), "top_k": top_k},
            )
            rows = result.fetchall()

        return [RetrievalResult(content=r.content, score=float(r.similarity), source=r.source) for r in rows]

    def _chunk_text(self, text: str) -> list[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + CHUNK_SIZE_CHARS
            chunks.append(text[start:end])
            start = end - CHUNK_OVERLAP_CHARS
        return chunks