
import hashlib, time, logging, sys
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend_service_layer.dependencies import current_user, db_session, DbSession
from backend_service_layer.auth import SessionUser
from backend_service_layer.workflows.agent_creation import AgentCreationRequest, create_agent_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/agents", tags=["agents"])

class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    persona: str | None = None
    capabilities: int = Field(default=0, ge=0)
    model_version: str = "v1"
    metadata_uri: str
    encrypted_data_hash_hex: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    endpoint: str | None = None
    idempotency_key: str | None = None

class CreateAgentDispatched(BaseModel):
    idempotency_key: str
    status_url: str
    progress: int = 0
    estimated_seconds: int = 30
    created_at: float = Field(default_factory=time.time)

class CreationProgress(BaseModel):
    idempotency_key: str
    status: str
    nft_id: int | None
    tx_hash: str | None
    progress: int
    estimated_seconds: int
    error_message: str | None
    created_at: float
    started_at: float | None
    finished_at: float | None

def _derive_key(user_id: str, body: CreateAgentRequest) -> str:
    return hashlib.sha256(f"{user_id}:{body.name}:{body.metadata_uri}:{body.encrypted_data_hash_hex}".encode()).hexdigest()[:32]

def _validate_uri(uri: str) -> bool:
    return uri.startswith(("ipfs://", "https://", "http://"))

@router.post("", response_model=CreateAgentDispatched, status_code=202)
async def create_agent(body: CreateAgentRequest, user: SessionUser = Depends(current_user), session: DbSession = Depends(db_session)):
    if not _validate_uri(body.metadata_uri):
        raise HTTPException(422, "metadata_uri must be ipfs:// or https://")
    existing = await session.execute(select(AgentCreationRequest).where(
        AgentCreationRequest.user_id == user.user_id,
        AgentCreationRequest.name == body.name,
        AgentCreationRequest.status.in_(["pending", "minting", "registering", "provisioning", "activating"])
    ))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Agent '{body.name}' already in progress")
    key = body.idempotency_key or _derive_key(user.user_id, body)
    req = AgentCreationRequest(
        idempotency_key=key, user_id=user.user_id, owner_address=user.wallet_address,
        name=body.name, persona=body.persona or "", capabilities=body.capabilities,
        model_version=body.model_version, metadata_uri=body.metadata_uri,
        encrypted_data_hash_hex=body.encrypted_data_hash_hex, endpoint=body.endpoint or "",
        status="pending", created_at=time.time()
    )
    session.add(req)
    await session.commit()
    create_agent_task.delay(
        idempotency_key=key, user_id=user.user_id, owner_address=user.wallet_address,
        name=body.name, persona=body.persona or "", capabilities=body.capabilities,
        model_version=body.model_version, metadata_uri=body.metadata_uri,
        encrypted_data_hash_hex=body.encrypted_data_hash_hex, endpoint=body.endpoint or ""
    )
    logger.info(f"Agent creation dispatched: {key} for user {user.user_id}")
    return CreateAgentDispatched(idempotency_key=key, status_url=f"/v1/agents/creation/{key}", progress=0, estimated_seconds=30)

@router.get("/creation/{key}", response_model=CreationProgress)
async def get_progress(key: str, session: DbSession = Depends(db_session)):
    req = (await session.execute(select(AgentCreationRequest).where(AgentCreationRequest.idempotency_key == key))).scalar_one_or_none()
    if not req:
        raise HTTPException(404, "Not found")
    status_map = {"pending":0, "minting":20, "registering":40, "provisioning":60, "activating":80, "completed":100, "failed":0}
    return CreationProgress(
        idempotency_key=key, status=req.status, nft_id=req.nft_id, tx_hash=req.tx_hash,
        progress=status_map.get(req.status, 0),
        estimated_seconds=30 if req.status in ["pending", "minting", "registering"] else 0,
        error_message=req.error_message, created_at=req.created_at,
        started_at=req.started_at, finished_at=req.finished_at
    )