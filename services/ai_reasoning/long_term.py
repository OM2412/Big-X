
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text as sql_text

from .embedding import EmbeddingClient

logger = logging.getLogger(__name__)


@dataclass
class Memory:
    summary: str
    outcome: str
    tool: str | None
    protocol: str | None
    relevance_score: float


class LongTermMemoryStore:
    def __init__(self, embedding_client: EmbeddingClient, db_session_factory):
        self.embedding_client = embedding_client
        self.db_session_factory = db_session_factory

    async def write_memory(
        self, agent_id: str, task_id: str, summary: str, outcome: str,
        tool: str | None = None, protocol: str | None = None,
        predicted_gas: int | None = None, actual_gas: int | None = None,
        predicted_slippage_bps: int | None = None, actual_slippage_bps: int | None = None,
    ) -> None:
        """Called by ReflectionAgent after Critic evaluates an execution —
        not called directly by Planner/Executor."""
        from db.models.ai_reasoning import AgentMemory

        embedding = await self.embedding_client.embed(summary)

        async with self.db_session_factory() as session:
            memory = AgentMemory(
                id=str(uuid.uuid4()), agent_id=agent_id, task_id=task_id,
                summary=summary, embedding=embedding, outcome=outcome,
                tool=tool, protocol=protocol,
                predicted_gas=predicted_gas, actual_gas=actual_gas,
                predicted_slippage_bps=predicted_slippage_bps, actual_slippage_bps=actual_slippage_bps,
            )
            session.add(memory)
            await session.commit()

        logger.info("Memory written for agent %s: %s", agent_id, summary[:80])

    async def recall_relevant(self, agent_id: str, query: str, top_k: int = 5) -> list[Memory]:
        """Called by PlannerAgent (via MemoryKnowledgeAgent) before
        planning — surfaces this agent's own past lessons relevant to the
        current request, e.g. "last time this agent bridged to Base, gas
        was underestimated by 15%"."""
        embedding = await self.embedding_client.embed(query)

        async with self.db_session_factory() as session:
            result = await session.execute(
                sql_text(
                    "SELECT summary, outcome, tool, protocol, "
                    "1 - (embedding <=> :query_embedding) AS similarity "
                    "FROM agent_memories WHERE agent_id = :agent_id "
                    "ORDER BY embedding <=> :query_embedding LIMIT :top_k"
                ),
                {"query_embedding": str(embedding), "agent_id": agent_id, "top_k": top_k},
            )
            rows = result.fetchall()

        return [
            Memory(summary=r.summary, outcome=r.outcome, tool=r.tool, protocol=r.protocol, relevance_score=float(r.similarity))
            for r in rows
        ]

    async def get_failure_patterns(self, agent_id: str, tool: str, limit: int = 10) -> list[Memory]:
        """Recent failures for a specific tool — used by PlanOptimizer to
        avoid repeating a pattern that's failed before, even if it wasn't
        semantically similar enough to surface via recall_relevant."""
        from db.models.ai_reasoning import AgentMemory

        async with self.db_session_factory() as session:
            stmt = (
                select(AgentMemory)
                .where(AgentMemory.agent_id == agent_id, AgentMemory.tool == tool, AgentMemory.outcome == "failure")
                .order_by(AgentMemory.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        return [Memory(summary=r.summary, outcome=r.outcome, tool=r.tool, protocol=r.protocol, relevance_score=1.0) for r in rows]