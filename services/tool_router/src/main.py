import logging
import os

from fastapi import FastAPI
from pydantic import BaseModel

from .tools.swap_tool import SwapTool
from .tools.bridge_tool import BridgeTool
from .tools.price_feed_tool import PriceFeedTool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Tool Router")

swap_tool = SwapTool()
bridge_tool = BridgeTool()
price_feed_tool = PriceFeedTool()


class DispatchRequest(BaseModel):
    agent_id: str
    tool: str
    action: str
    params: dict


class DispatchResponse(BaseModel):
    status: str
    result: dict


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "tool-router"}


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "tool-router"}


@app.post("/tool/dispatch", response_model=DispatchResponse)
async def dispatch(payload: DispatchRequest) -> DispatchResponse:
    if payload.tool == "swap_tool":
        result = await swap_tool.quote(payload.params.get("src", ""), payload.params.get("dst", ""), float(payload.params.get("amount_usd", 0)))
        return DispatchResponse(status="dispatched", result=result)
    if payload.tool == "bridge_tool":
        result = await bridge_tool.quote(payload.params.get("token", ""), payload.params.get("src", ""), payload.params.get("dst", ""), float(payload.params.get("amount", 0)), payload.params.get("to", ""), auth=payload.params.get("auth", ""))
        return DispatchResponse(status="dispatched", result=result)
    if payload.tool == "price_feed_tool":
        result = await price_feed_tool.get_price(payload.params.get("symbol", "BTC"))
        return DispatchResponse(status="dispatched", result=result)

    return DispatchResponse(status="unsupported", result={"tool": payload.tool})
