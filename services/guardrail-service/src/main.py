import logging
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis

from .abuse_detection import AbuseDetectionService
from .context_sanitization import ContextSanitizer
from .intent_check import IntentCheckAgent
from .policy_check import PolicyCheckService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Guardrail Service")

redis_client = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"), decode_responses=True)

intent_agent = IntentCheckAgent()
context_sanitizer = ContextSanitizer()
policy_check_service = PolicyCheckService(db_session_factory=lambda: None)
abuse_detection_service = AbuseDetectionService(redis_client=redis_client)


class GuardrailRequest(BaseModel):
    task_id: str
    agent_id: str
    message: str
    metadata: dict | None = None


class GuardrailResponse(BaseModel):
    allowed: bool
    status: str
    reason: str | None = None


@app.get("/health")
async def health() -> dict:
    try:
        await redis_client.ping()
        return {"status": "ok", "service": "guardrail-service"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/ready")
async def readiness() -> dict:
    return {"status": "ready", "service": "guardrail-service"}


@app.post("/guardrail/check", response_model=GuardrailResponse)
async def check_guardrails(payload: GuardrailRequest) -> GuardrailResponse:
    sanitized_message = context_sanitizer.sanitize(payload.message)
    intent_result = await intent_agent.check(sanitized_message)
    if not intent_result.allowed:
        return GuardrailResponse(allowed=False, status="blocked", reason=intent_result.reason)

    abuse_result = await abuse_detection_service.check(payload.agent_id, sanitized_message)
    if not abuse_result.allowed:
        return GuardrailResponse(allowed=False, status="blocked", reason=abuse_result.reason)

    policy_result = await policy_check_service.check(payload.agent_id, "defi_action")
    if not policy_result.allowed:
        return GuardrailResponse(allowed=False, status="blocked", reason=policy_result.reason)

    return GuardrailResponse(allowed=True, status="allowed")
