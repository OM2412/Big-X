import logging

from .state import AgentState

logger = logging.getLogger(__name__)

class MemoryKnowledgeAgent:
    def __init__(self, vector_store, db_session_factory, portfolio_client):
        self.vector_store = vector_store              # e.g. a Pinecone/pgvector wrapper
        self.db_session_factory = db_session_factory    # for querying execution_history
        self.portfolio_client = portfolio_client         # reads current on-chain balances

    async def run(self, state: AgentState) -> AgentState:
        logger.info("Memory: gathering context for task %s", state["task_id"])

        agent_id = state["agent_id"]

        portfolio = await self.portfolio_client.get_portfolio(agent_id)
        history = await self._get_recent_history(agent_id, limit=10)
        rag_results = await self._retrieve_relevant_docs(state["user_request"])

        state["context"] = {
            "portfolio": portfolio,
            "recent_history": history,
            "relevant_docs": rag_results,
        }
        return state

    async def _get_recent_history(self, agent_id: str, limit: int) -> list[dict]:
        async with self.db_session_factory() as session:
            # TODO: replace with a real query against db.models.execution_history.ExecutionStep
            # filtered by agent_id, ordered by created_at desc, limited to `limit`.
            return []

    async def _retrieve_relevant_docs(self, query: str, top_k: int = 5) -> list[dict]:
        embedding = await self.vector_store.embed(query)
        results = await self.vector_store.query(embedding, top_k=top_k)
        return [{"content": r.content, "score": r.score} for r in results]