import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .mpc_wallet import MpcWalletClient
from .spend_limits import SpendLimitsService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Wallet Service")

mpc_wallet = MpcWalletClient()
spend_limits = SpendLimitsService(redis_client=None, config_store=None)


class WalletRequest(BaseModel):
    agent_id: str
    amount_usd: float


class WalletResponse(BaseModel):
    allowed: bool
    reason: str | None = None
    remaining_daily_usd: float | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "wallet-service"}


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "wallet-service"}


@app.post("/wallet/spend-check", response_model=WalletResponse)
async def spend_check(payload: WalletRequest) -> WalletResponse:
    try:
        result = await spend_limits.check(payload.agent_id, payload.amount_usd)
        return WalletResponse(allowed=result.allowed, reason=result.reason, remaining_daily_usd=result.remaining_daily_usd)
    except Exception as exc:
        logger.exception("Spend limit check failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
