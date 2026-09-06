import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.agents import Agent, LifecycleState

_LIFECYCLE_STATES = ["created", "provisioning", "active", "suspended", "deprecated", "archived"]


class AgentNotFoundError(Exception):
    pass


class AgentService:
    def __init__(self, agent_registry_client):
        self.agent_registry_client = agent_registry_client

    async def list_for_user(self, session: AsyncSession, user_id: str) -> list[Agent]:
        stmt = select(Agent).where(Agent.owner_id == uuid.UUID(user_id))
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_owned(self, session: AsyncSession, agent_id: uuid.UUID, user_id: str) -> Agent:
        agent = await session.get(Agent, agent_id)
        if agent is None or agent.owner_id != uuid.UUID(user_id):
            raise AgentNotFoundError(str(agent_id))
        return agent

    async def refresh_from_chain(self, session: AsyncSession, agent_id: uuid.UUID, user_id: str) -> Agent:
        """The synchronous version — kept for callers that genuinely need
        to wait (e.g. an internal service call). The HTTP route uses the
        Celery task version (tasks.refresh_agent_task) instead, so a slow
        RPC never holds an HTTP connection open."""
        agent = await self.get_owned(session, agent_id, user_id)

        onchain = self.agent_registry_client.get_agent(agent.nft_id)
        agent.capabilities = onchain.capabilities
        agent.state = LifecycleState(_LIFECYCLE_STATES[onchain.state])
        agent.token_bound_account = onchain.token_bound_account
        agent.last_synced_at = datetime.utcnow()

        await session.commit()
        await session.refresh(agent)
        return agent