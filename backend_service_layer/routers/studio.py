from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..dependencies import current_user, db_session, DbSession
from ..auth import SessionUser

router = APIRouter(prefix="/studio", tags=["studio"])


class CreateAgentRequest(BaseModel):
    name: str
    persona: str | None = None
    model_version: str = "default"
    metadata_uri: str | None = None
    endpoint: str | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    persona: str | None = None
    model_version: str | None = None
    metadata_uri: str | None = None
    endpoint: str | None = None
    capabilities: int | None = None


class AgentResponse(BaseModel):
    id: str
    name: str
    persona: str | None = None
    model_version: str
    metadata_uri: str | None = None
    endpoint: str | None = None
    capabilities: int
    state: str
    nft_id: int | None = None
    token_bound_account: str | None = None
    creator_wallet: str


@router.get("/agents", response_model=list[AgentResponse])
async def list_my_agents(
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.agents import Agent

    result = await session.execute(select(Agent).where(Agent.owner_id == user.user_id))
    agents = result.scalars().all()
    return [
        AgentResponse(
            id=str(a.id),
            name=a.name,
            persona=a.persona,
            model_version=a.model_version,
            metadata_uri=a.metadata_uri,
            endpoint=a.endpoint,
            capabilities=a.capabilities,
            state=a.state.value if hasattr(a.state, "value") else str(a.state),
            nft_id=a.nft_id,
            token_bound_account=a.token_bound_account,
            creator_wallet=a.creator_wallet,
        )
        for a in agents
    ]


@router.post("/agents", response_model=AgentResponse)
async def create_agent(
    req: CreateAgentRequest,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    import uuid
    from db.models.agents import Agent

    agent = Agent(
        id=uuid.uuid4(),
        owner_id=user.user_id,
        creator_wallet=user.wallet_address or "",
        name=req.name,
        persona=req.persona,
        model_version=req.model_version,
        metadata_uri=req.metadata_uri or "",
        endpoint=req.endpoint,
        nft_id=0,
        capabilities=0,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)

    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        persona=agent.persona,
        model_version=agent.model_version,
        metadata_uri=agent.metadata_uri,
        endpoint=agent.endpoint,
        capabilities=agent.capabilities,
        state=agent.state.value if hasattr(agent.state, "value") else str(agent.state),
        nft_id=agent.nft_id,
        token_bound_account=agent.token_bound_account,
        creator_wallet=agent.creator_wallet,
    )


@router.patch("/agents/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    req: UpdateAgentRequest,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.agents import Agent

    result = await session.execute(select(Agent).where(Agent.id == agent_id, Agent.owner_id == user.user_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    if req.name is not None:
        agent.name = req.name
    if req.persona is not None:
        agent.persona = req.persona
    if req.model_version is not None:
        agent.model_version = req.model_version
    if req.metadata_uri is not None:
        agent.metadata_uri = req.metadata_uri
    if req.endpoint is not None:
        agent.endpoint = req.endpoint
    if req.capabilities is not None:
        agent.capabilities = req.capabilities

    await session.commit()
    await session.refresh(agent)

    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        persona=agent.persona,
        model_version=agent.model_version,
        metadata_uri=agent.metadata_uri,
        endpoint=agent.endpoint,
        capabilities=agent.capabilities,
        state=agent.state.value if hasattr(agent.state, "value") else str(agent.state),
        nft_id=agent.nft_id,
        token_bound_account=agent.token_bound_account,
        creator_wallet=agent.creator_wallet,
    )


@router.post("/agents/{agent_id}/publish", response_model=AgentResponse)
async def publish_agent(
    agent_id: str,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.agents import Agent, LifecycleState

    result = await session.execute(select(Agent).where(Agent.id == agent_id, Agent.owner_id == user.user_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.state = LifecycleState.ACTIVE
    await session.commit()
    await session.refresh(agent)

    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        persona=agent.persona,
        model_version=agent.model_version,
        metadata_uri=agent.metadata_uri,
        endpoint=agent.endpoint,
        capabilities=agent.capabilities,
        state=agent.state.value if hasattr(agent.state, "value") else str(agent.state),
        nft_id=agent.nft_id,
        token_bound_account=agent.token_bound_account,
        creator_wallet=agent.creator_wallet,
    )


@router.post("/agents/{agent_id}/unpublish", response_model=AgentResponse)
async def unpublish_agent(
    agent_id: str,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.agents import Agent, LifecycleState

    result = await session.execute(select(Agent).where(Agent.id == agent_id, Agent.owner_id == user.user_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.state = LifecycleState.SUSPENDED
    await session.commit()
    await session.refresh(agent)

    return AgentResponse(
        id=str(agent.id),
        name=agent.name,
        persona=agent.persona,
        model_version=agent.model_version,
        metadata_uri=agent.metadata_uri,
        endpoint=agent.endpoint,
        capabilities=agent.capabilities,
        state=agent.state.value if hasattr(agent.state, "value") else str(agent.state),
        nft_id=agent.nft_id,
        token_bound_account=agent.token_bound_account,
        creator_wallet=agent.creator_wallet,
    )
