import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .bridge_client import BridgeClient
from .btc_custody import BtcCustodyMonitor
from .peg_out_redemption import PegOutRedemptionService
from .wrapped_assets import WrappedAssetClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Bridging Service")

bridge_client = BridgeClient()
wrapped_asset_client = WrappedAssetClient()


class BridgeRequest(BaseModel):
    recipient_evm_address: str
    amount_btc: float


class BridgeResponse(BaseModel):
    status: str
    deposit_address: str | None = None
    tx_hash: str | None = None


@app.get("/health")
async def health() -> dict:
    try:
        w3 = bridge_client.w3
        if not w3.is_connected():
            raise RuntimeError("EVM RPC not connected")
        return {"status": "ok", "service": "bridging-service"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "bridging-service"}


@app.post("/bridge/peg-in", response_model=BridgeResponse)
async def peg_in(payload: BridgeRequest) -> BridgeResponse:
    try:
        deposit = bridge_client.confirm_peg_in(
            btc_tx_hash=b"",
            recipient=payload.recipient_evm_address,
            amount=int(payload.amount_btc * 10**8),
        )
        return BridgeResponse(status="initiated", tx_hash=deposit.tx_hash.hex())
    except Exception as exc:
        logger.exception("Peg-in failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
