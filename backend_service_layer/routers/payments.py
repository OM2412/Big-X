from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..dependencies import current_user, db_session, DbSession
from ..auth import SessionUser

router = APIRouter(prefix="/payments", tags=["payments"])


class PaymentIntentRequest(BaseModel):
    amount: float
    currency: str = "ETH"
    agent_id: str | None = None
    provider: str = "crypto"


class PaymentIntentResponse(BaseModel):
    payment_id: str
    status: str
    amount: float
    currency: str
    provider: str
    message: str


@router.post("/intent", response_model=PaymentIntentResponse)
async def create_payment_intent(
    req: PaymentIntentRequest,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    import uuid
    from db.models.transactions import Transaction, TransactionType, TransactionStatus

    tx = Transaction(
        agent_id=req.agent_id or "",
        tx_hash="",
        chain_id=8453,
        tx_type=TransactionType.NFT_TRADE,
        status=TransactionStatus.PENDING,
        from_address=user.wallet_address or "",
        to_address="",
        amount=req.amount,
    )
    session.add(tx)
    await session.commit()

    return PaymentIntentResponse(
        payment_id=str(tx.id),
        status="pending",
        amount=req.amount,
        currency=req.currency,
        provider=req.provider,
        message=f"Payment intent created for {req.amount} {req.currency} via {req.provider}.",
    )


@router.post("/confirm")
async def confirm_payment(
    payment_id: str,
    tx_hash: str,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    from sqlalchemy import select
    from db.models.transactions import Transaction

    result = await session.execute(select(Transaction).where(Transaction.id == payment_id))
    tx = result.scalar_one_or_none()
    if not tx:
        raise HTTPException(status_code=404, detail="Payment not found")

    tx.tx_hash = tx_hash
    tx.status = TransactionStatus.SUBMITTED
    from datetime import datetime
    tx.submitted_at = datetime.utcnow()
    await session.commit()

    return {"status": "confirmed", "payment_id": payment_id, "tx_hash": tx_hash}


class FiatPaymentRequest(BaseModel):
    amount: float
    currency: str = "USD"
    agent_id: str | None = None
    provider: str = "stripe"


@router.post("/fiat", response_model=PaymentIntentResponse)
async def fiat_payment(
    req: FiatPaymentRequest,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    import uuid
    from db.models.transactions import Transaction, TransactionType, TransactionStatus

    tx = Transaction(
        agent_id=req.agent_id or "",
        tx_hash="",
        chain_id=0,
        tx_type=TransactionType.NFT_TRADE,
        status=TransactionStatus.PENDING,
        from_address=user.wallet_address or "",
        to_address="",
        amount_usd=req.amount,
    )
    session.add(tx)
    await session.commit()

    return PaymentIntentResponse(
        payment_id=str(tx.id),
        status="pending_fiat",
        amount=req.amount,
        currency=req.currency,
        provider=req.provider,
        message=f"Fiat payment intent created for {req.amount} {req.currency} via {req.provider}.",
    )
