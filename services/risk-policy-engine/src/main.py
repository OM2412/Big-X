import logging
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .policy_engine import PolicyEngineClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Risk Policy Engine")

policy_engine_client = PolicyEngineClient()


class PolicyEvaluationRequest(BaseModel):
    agent_id: str
    target: str
    value: int
    data: str


class PolicyEvaluationResponse(BaseModel):
    allowed: bool
    reason: str | None = None


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "risk-policy-engine"}


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "risk-policy-engine"}


@app.post("/policy/check", response_model=PolicyEvaluationResponse)
async def check_policy(payload: PolicyEvaluationRequest) -> PolicyEvaluationResponse:
    try:
        allowed, reason = await policy_engine_client.check_action(payload.agent_id, payload.target, payload.value, bytes.fromhex(payload.data))
        return PolicyEvaluationResponse(allowed=allowed, reason=reason)
    except Exception as exc:
        logger.exception("Policy evaluation failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
