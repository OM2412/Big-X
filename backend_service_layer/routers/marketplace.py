from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..dependencies import current_user, db_session, DbSession
from ..auth import SessionUser

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


class MarketplaceAgentResponse(BaseModel):
    id: str
    name: str
    persona: str | None = None
    model_version: str
    price: float | None = None
    currency: str = "ETH"
    creator_wallet: str
    nft_id: int | None = None
    state: str
    metadata_uri: str | None = None


class BuyRequest(BaseModel):
    agent_id: str
    payment_method: str = "crypto"


class BuyResponse(BaseModel):
    tx_hash: str | None = None
    status: str
    message: str


@router.get("/agents", response_model=list[MarketplaceAgentResponse])
async def list_marketplace_agents(
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.agents import Agent
    from db.models.orders_listings import Listing

    result = await session.execute(
        select(Agent, Listing)
        .join(Listing, Agent.id == Listing.agent_id)
        .where(Listing.status == "active")
    )
    rows = result.all()

    agents = []
    for agent, listing in rows:
        agents.append(
            MarketplaceAgentResponse(
                id=str(agent.id),
                name=agent.name,
                persona=agent.persona,
                model_version=agent.model_version,
                price=float(listing.price) if listing.price is not None else None,
                currency="ETH",
                creator_wallet=agent.creator_wallet,
                nft_id=agent.nft_id,
                state=agent.state.value if hasattr(agent.state, "value") else str(agent.state),
                metadata_uri=agent.metadata_uri,
            )
        )
    return agents


@router.get("/my-purchases", response_model=list[MarketplaceAgentResponse])
async def my_purchases(
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.agents import Agent
    from db.models.orders_listings import Listing

    result = await session.execute(
        select(Agent, Listing)
        .join(Listing, Agent.id == Listing.agent_id)
        .where(Listing.buyer_id == user.user_id, Listing.status == "sold")
    )
    rows = result.all()

    agents = []
    for agent, listing in rows:
        agents.append(
            MarketplaceAgentResponse(
                id=str(agent.id),
                name=agent.name,
                persona=agent.persona,
                model_version=agent.model_version,
                price=float(listing.price) if listing.price is not None else None,
                currency="ETH",
                creator_wallet=agent.creator_wallet,
                nft_id=agent.nft_id,
                state=agent.state.value if hasattr(agent.state, "value") else str(agent.state),
                metadata_uri=agent.metadata_uri,
            )
        )
    return agents


@router.post("/buy", response_model=BuyResponse)
async def buy_agent(
    req: BuyRequest,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from db.models.orders_listings import Listing, ListingStatus
    from sqlalchemy import select

    result = await session.execute(
        select(Listing).where(Listing.id == req.agent_id, Listing.status == ListingStatus.ACTIVE)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found or already sold")

    listing.buyer_id = user.user_id
    listing.status = ListingStatus.SOLD
    from datetime import datetime
    listing.sold_at = datetime.utcnow()
    await session.commit()

    return BuyResponse(
        tx_hash=None,
        status="pending_on_chain",
        message="Purchase recorded. Complete payment to transfer ownership.",
    )
