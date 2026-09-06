from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..dependencies import current_user, db_session, DbSession
from ..auth import SessionUser

router = APIRouter(prefix="/smart-contracts", tags=["smart-contracts"])


class TransactionResponse(BaseModel):
    tx_hash: str
    status: str
    contract: str
    message: str


@router.post("/transfer", response_model=TransactionResponse)
async def transfer_agent(
    agent_id: str,
    to_address: str,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.agents import Agent

    result = await session.execute(select(Agent).where(Agent.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    return TransactionResponse(
        tx_hash="",
        status="pending",
        contract="AgentRegistry",
        message=f"Transfer of agent {agent_id} to {to_address} initiated.",
    )


@router.get("/transactions/{agent_id}")
async def get_agent_transactions(
    agent_id: str,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.transactions import Transaction

    result = await session.execute(
        select(Transaction).where(Transaction.agent_id == agent_id).order_by(Transaction.created_at.desc())
    )
    txs = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "tx_hash": t.tx_hash,
            "tx_type": t.tx_type.value if hasattr(t.tx_type, "value") else str(t.tx_type),
            "status": t.status.value if hasattr(t.status, "value") else str(t.status),
            "chain_id": t.chain_id,
            "amount": float(t.amount) if t.amount is not None else None,
            "confirmed_at": t.confirmed_at.isoformat() if t.confirmed_at else None,
        }
        for t in txs
    ]


@router.post("/list", response_model=TransactionResponse)
async def list_agent(
    agent_id: str,
    price_eth: float,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from db.models.orders_listings import Listing
    from datetime import datetime

    listing = Listing(
        agent_id=agent_id,
        seller_id=user.user_id,
        price=price_eth,
        status="active",
        listed_at=datetime.utcnow(),
    )
    session.add(listing)
    await session.commit()

    return TransactionResponse(
        tx_hash="",
        status="pending",
        contract="Marketplace",
        message=f"Agent {agent_id} listed for {price_eth} ETH.",
    )


@router.delete("/list/{listing_id}", response_model=TransactionResponse)
async def cancel_listing(
    listing_id: str,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from db.models.orders_listings import Listing, ListingStatus

    result = await session.execute(
        select(Listing).where(Listing.id == listing_id, Listing.seller_id == user.user_id)
    )
    listing = result.scalar_one_or_none()
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    listing.status = ListingStatus.CANCELLED
    from datetime import datetime
    listing.cancelled_at = datetime.utcnow()
    await session.commit()

    return TransactionResponse(
        tx_hash="",
        status="cancelled",
        contract="Marketplace",
        message=f"Listing {listing_id} cancelled.",
    )
