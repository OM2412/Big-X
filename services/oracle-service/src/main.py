import logging
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from web3 import Web3

from .chainlink_client import ChainlinkClient
from .redstone_client import RedstoneClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Oracle Service")

web3 = Web3(Web3.HTTPProvider(os.getenv("CHAIN_RPC_URL", "http://localhost:8545")))
chainlink_client = ChainlinkClient(web3)
redstone_client = RedstoneClient()


class PriceRequest(BaseModel):
    chain: str
    pair: str
    symbol: str | None = None


class PriceResponse(BaseModel):
    price: float
    source: str


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "oracle-service"}


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "oracle-service"}


@app.post("/oracle/price", response_model=PriceResponse)
async def get_price(payload: PriceRequest) -> PriceResponse:
    try:
        if payload.pair:
            result = chainlink_client.get_price(payload.chain, payload.pair)
            return PriceResponse(price=result.price, source=result.source)
        result = await redstone_client.get_price(payload.symbol or "BTC")
        return PriceResponse(price=result.price, source=result.source)
    except Exception as exc:
        logger.exception("Price lookup failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
