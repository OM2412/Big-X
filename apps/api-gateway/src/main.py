# apps/api-gateway/src/main.py
import logging
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .middleware.auth import SessionUser, get_current_user
from .middleware.rbac import rbac_middleware
from .routes import agents_router, auth_router
from .integration import GatewayIntegration
from db.session import get_db_session
from db.models.agents import Agent, LifecycleState
from db.models.users import User
from db.models.orders_listings import Listing, ListingStatus
from db.models.transactions import Transaction, TransactionType, TransactionStatus
from db.models.nft_metadata import NFTMetadata
from sqlalchemy import select, func, desc
from shared.api.error_handling import register_error_handlers
from shared.api.headers import RequestIDMiddleware
from shared.api.rate_limit import InMemoryRateLimiter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API gateway starting up...")
    try:
        from db.database import wait_for_database, init_db_schema
        await wait_for_database(max_attempts=30, delay_seconds=1.0)
        await init_db_schema()
        logger.info("Database initialized")
    except Exception as exc:
        logger.warning("Database initialization failed: %s", exc)
    yield
    logger.info("API gateway shutting down...")


app = FastAPI(title="Agentic DeFi API Gateway", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.environ.get("WEB_APP_ORIGIN", "http://localhost:3000"),
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestIDMiddleware)
app.add_middleware(InMemoryRateLimiter, limit=120, window_seconds=60)
app.middleware("http")(rbac_middleware)

register_error_handlers(app)

app.include_router(auth_router, prefix="/api/v1")
app.include_router(agents_router, prefix="/api/v1")

integration = GatewayIntegration()


# --------------------------------------------------------------------------
# Request / Response models
# --------------------------------------------------------------------------

class WorkflowRequest(BaseModel):
    agent_id: str = Field(..., description="The agent identifier to execute.")
    message: str = Field(..., description="The user request or task payload.")
    metadata: dict[str, Any] | None = Field(default=None, description="Optional metadata passed along to downstream services.")


class WorkflowResponse(BaseModel):
    task_id: str = Field(..., description="Unique identifier for the workflow execution.")
    status: str = Field(..., description="Current workflow status.")
    message: str = Field(..., description="Human-readable workflow summary.")
    steps: list[dict[str, Any]] = Field(default_factory=list, description="List of downstream service steps executed.")


class ChatRequest(BaseModel):
    agent_id: str = Field(..., description="The agent identifier to chat with.")
    message: str = Field(..., description="User message.")


class ChatResponse(BaseModel):
    task_id: str
    status: str
    reply: str
    steps: list[dict[str, Any]]


class MarketplaceAgent(BaseModel):
    id: str
    name: str
    state: str
    nft_id: int
    persona: str | None = None
    model_version: str
    metadata_uri: str
    endpoint: str | None = None
    token_bound_account: str | None = None
    capabilities: int
    creator_wallet: str
    price: float | None = None
    seller: str | None = None


class BuyRequest(BaseModel):
    agent_id: str
    payment_method: str = Field(..., description="fiat or crypto")
    amount: float | None = None


class CreateAgentRequest(BaseModel):
    name: str
    persona: str | None = None
    model_version: str = "default"
    metadata_uri: str
    endpoint: str | None = None
    capabilities: int = 0
    price: float | None = None


class UpdateAgentRequest(BaseModel):
    name: str | None = None
    persona: str | None = None
    model_version: str | None = None
    metadata_uri: str | None = None
    endpoint: str | None = None
    capabilities: int | None = None
    price: float | None = None


class DashboardResponse(BaseModel):
    wallet_balance: float
    native_balance: str
    published_agents: int
    purchased_agents: int
    revenue: float
    recent_activities: list[dict[str, Any]]


class PaymentFiatRequest(BaseModel):
    agent_id: str
    provider: str = Field(..., description="razorpay or stripe")
    amount: float
    currency: str = "usd"


class PaymentCryptoRequest(BaseModel):
    agent_id: str
    amount: float


class PaymentResponse(BaseModel):
    status: str
    tx_hash: str | None = None
    message: str


# --------------------------------------------------------------------------
# Health / readiness
# --------------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "service": "api-gateway"}


@app.get("/api/v1/health")
def health_v1() -> dict[str, Any]:
    return health()


@app.get("/ready")
async def readiness() -> dict[str, Any]:
    return {"status": "ready", "service": "api-gateway", "environment": os.getenv("APP_ENV", "development")}


# --------------------------------------------------------------------------
# Workflow / Chat execution
# --------------------------------------------------------------------------

@app.post("/api/v1/execute", response_model=WorkflowResponse)
async def execute_workflow(payload: WorkflowRequest, user: SessionUser = Depends(get_current_user)) -> WorkflowResponse:
    try:
        result = await integration.execute_pipeline(payload.agent_id, payload.message, payload.metadata or {})
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive integration path
        logger.exception("Workflow execution failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return WorkflowResponse(
        task_id=result["task_id"],
        status=result["status"],
        message=result["message"],
        steps=result["steps"],
    )


@app.post("/api/v1/tool/dispatch")
async def proxy_tool_dispatch(payload: dict[str, Any], user: SessionUser = Depends(get_current_user)) -> dict[str, Any]:
    try:
        return await integration.proxy_request("tool-router", "/tool/dispatch", payload)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive integration path
        logger.exception("Tool dispatch proxy failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Dashboard Integration
# --------------------------------------------------------------------------

@app.get("/v1/dashboard", response_model=DashboardResponse)
async def get_dashboard(user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        wallet_balance = 0.0
        native_balance = "0"
        try:
            wallet_resp = await integration.proxy_request("wallet-service", "/wallet/spend-check", {"agent_id": "dashboard", "amount_usd": 0})
            wallet_balance = float(wallet_resp.get("remaining_daily_usd", 0))
        except Exception:
            logger.debug("Wallet service unavailable for dashboard", exc_info=True)

        user_uuid = user.user_id
        if not user_uuid:
            user_uuid = user.wallet_address or "unknown"

        published_stmt = select(func.count(Agent.id)).where(Agent.creator_wallet == (user.wallet_address or ""))
        published_result = await session.execute(published_stmt)
        published_agents = published_result.scalar_one_or_none() or 0

        purchased_stmt = select(func.count(Agent.id)).where(Agent.owner_id == user_uuid)
        purchased_result = await session.execute(purchased_stmt)
        purchased_agents = purchased_result.scalar_one_or_none() or 0

        revenue_stmt = select(func.coalesce(func.sum(Transaction.amount_usd), 0.0)).where(
            Transaction.agent_id.in_(select(Agent.id).where(Agent.owner_id == user_uuid)),
            Transaction.status == TransactionStatus.CONFIRMED,
        )
        revenue_result = await session.execute(revenue_stmt)
        revenue = float(revenue_result.scalar_one_or_none() or 0.0)

        activities_stmt = (
            select(Transaction)
            .where(Transaction.agent_id.in_(select(Agent.id).where(Agent.owner_id == user_uuid)))
            .order_by(desc(Transaction.created_at))
            .limit(10)
        )
        activities_result = await session.execute(activities_stmt)
        recent_activities = []
        for tx in activities_result.scalars().all():
            recent_activities.append({
                "id": str(tx.id),
                "type": tx.tx_type.value,
                "status": tx.status.value,
                "amount_usd": float(tx.amount_usd) if tx.amount_usd else 0.0,
                "tx_hash": tx.tx_hash,
                "created_at": tx.created_at.isoformat() if tx.created_at else None,
            })

        return DashboardResponse(
            wallet_balance=wallet_balance,
            native_balance=native_balance,
            published_agents=published_agents,
            purchased_agents=purchased_agents,
            revenue=revenue,
            recent_activities=recent_activities,
        )
    except Exception as exc:
        logger.exception("Dashboard fetch failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Marketplace Integration
# --------------------------------------------------------------------------

@app.get("/v1/marketplace", response_model=list[MarketplaceAgent])
async def list_marketplace(
    user: SessionUser = Depends(get_current_user),
    session=Depends(get_db_session),
    q: str | None = Query(None, description="Search query"),
):
    try:
        stmt = (
            select(Agent, NFTMetadata, User, Listing)
            .join(NFTMetadata, Agent.id == NFTMetadata.agent_id, isouter=True)
            .join(User, Agent.owner_id == User.id, isouter=True)
            .join(Listing, Agent.id == Listing.agent_id, isouter=True)
            .where(Listing.status == ListingStatus.ACTIVE)
        )
        if q:
            stmt = stmt.where(Agent.name.ilike(f"%{q}%"))

        stmt = stmt.order_by(desc(Agent.created_at)).limit(100)
        result = await session.execute(stmt)
        rows = result.all()

        agents = []
        for agent, nft, user_row, listing in rows:
            agents.append(MarketplaceAgent(
                id=str(agent.id),
                name=agent.name,
                state=agent.state.value,
                nft_id=agent.nft_id,
                persona=agent.persona,
                model_version=agent.model_version,
                metadata_uri=agent.metadata_uri,
                endpoint=agent.endpoint,
                token_bound_account=agent.token_bound_account,
                capabilities=agent.capabilities,
                creator_wallet=agent.creator_wallet,
                price=float(listing.price) if listing else None,
                seller=user_row.wallet_address if user_row else None,
            ))
        return agents
    except Exception as exc:
        logger.exception("Marketplace list failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/v1/marketplace/agents", response_model=list[MarketplaceAgent])
async def list_marketplace_agents_v1(
    user: SessionUser = Depends(get_current_user),
    session=Depends(get_db_session),
    q: str | None = Query(None, description="Search query"),
):
    return await list_marketplace(user=user, session=session, q=q)


@app.get("/v1/marketplace/my-purchases")
async def list_my_purchases_v1(user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    stmt = select(Agent).where(Agent.owner_id == user.user_id).order_by(desc(Agent.created_at))
    result = await session.execute(stmt)
    agents = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "name": a.name,
            "state": a.state.value,
            "nft_id": a.nft_id,
            "persona": a.persona,
            "model_version": a.model_version,
            "metadata_uri": a.metadata_uri,
            "endpoint": a.endpoint,
            "capabilities": a.capabilities,
            "creator_wallet": a.creator_wallet,
            "token_bound_account": a.token_bound_account,
        }
        for a in agents
    ]


@app.get("/v1/marketplace/{agent_id}", response_model=MarketplaceAgent)
async def get_marketplace_agent(agent_id: str, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        agent_uuid = __import__("uuid").UUID(agent_id)
        stmt = (
            select(Agent, NFTMetadata, User, Listing)
            .join(NFTMetadata, Agent.id == NFTMetadata.agent_id, isouter=True)
            .join(User, Agent.owner_id == User.id, isouter=True)
            .join(Listing, Agent.id == Listing.agent_id, isouter=True)
            .where(Agent.id == agent_uuid)
        )
        result = await session.execute(stmt)
        row = result.first()
        if not row:
            raise HTTPException(status_code=404, detail="Agent not found")

        agent, nft, user_row, listing = row
        return MarketplaceAgent(
            id=str(agent.id),
            name=agent.name,
            state=agent.state.value,
            nft_id=agent.nft_id,
            persona=agent.persona,
            model_version=agent.model_version,
            metadata_uri=agent.metadata_uri,
            endpoint=agent.endpoint,
            token_bound_account=agent.token_bound_account,
            capabilities=agent.capabilities,
            creator_wallet=agent.creator_wallet,
            price=float(listing.price) if listing else None,
            seller=user_row.wallet_address if user_row else None,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Marketplace get failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/v1/search")
async def search_agents(q: str = Query(""), user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        stmt = (
            select(Agent, NFTMetadata)
            .join(NFTMetadata, Agent.id == NFTMetadata.agent_id, isouter=True)
            .where(Agent.name.ilike(f"%{q}%"))
            .limit(50)
        )
        result = await session.execute(stmt)
        rows = result.all()
        return {
            "query": q,
            "results": [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "state": a.state.value,
                    "nft_id": a.nft_id,
                    "persona": a.persona,
                    "model_version": a.model_version,
                }
                for a, _ in rows
            ],
        }
    except Exception as exc:
        logger.exception("Search failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/buy")
async def buy_agent(payload: BuyRequest, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        agent_uuid = __import__("uuid").UUID(payload.agent_id)
        stmt = select(Listing).where(Listing.agent_id == agent_uuid, Listing.status == ListingStatus.ACTIVE)
        result = await session.execute(stmt)
        listing = result.scalar_one_or_none()
        if not listing:
            raise HTTPException(status_code=404, detail="Agent not listed for sale")

        agent = await session.get(Agent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        if payload.payment_method == "crypto":
            blockchain_result = await integration.proxy_request("smart-contract-integration", "/contracts/buy", {
                "nft_id": agent.nft_id,
                "buyer": user.wallet_address or "",
                "price": payload.amount or float(listing.price),
            })
            tx_hash = blockchain_result.get("tx_hash")
        else:
            tx_hash = f"fiat-{__import__('uuid').uuid4().hex}"

        listing.status = ListingStatus.SOLD
        listing.buyer_id = user.user_id if hasattr(user, 'user_id') else None
        listing.sold_at = __import__("datetime").datetime.utcnow()
        listing.tx_hash = tx_hash

        agent.owner_id = user.user_id if hasattr(user, 'user_id') else __import__("uuid").UUID(user.user_id)

        tx = Transaction(
            agent_id=agent.id,
            tx_hash=tx_hash,
            chain_id=8453,
            tx_type=TransactionType.NFT_TRADE,
            status=TransactionStatus.CONFIRMED if payload.payment_method == "crypto" else TransactionStatus.PENDING,
            from_address=listing.seller_id,
            to_address=user.wallet_address or "",
            amount=float(listing.price),
            amount_usd=float(listing.price),
            confirmed_at=__import__("datetime").datetime.utcnow() if payload.payment_method == "crypto" else None,
        )
        session.add(tx)
        await session.commit()

        return {"status": "success", "tx_hash": tx_hash, "agent_id": payload.agent_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Buy agent failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Agent Studio
# --------------------------------------------------------------------------

@app.get("/v1/my-agents")
async def list_my_agents(user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        stmt = select(Agent).where(Agent.owner_id == user.user_id).order_by(desc(Agent.created_at))
        result = await session.execute(stmt)
        agents = result.scalars().all()
        return {
            "agents": [
                {
                    "id": str(a.id),
                    "name": a.name,
                    "state": a.state.value,
                    "nft_id": a.nft_id,
                    "persona": a.persona,
                    "model_version": a.model_version,
                    "metadata_uri": a.metadata_uri,
                    "endpoint": a.endpoint,
                    "capabilities": a.capabilities,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                }
                for a in agents
            ]
        }
    except Exception as exc:
        logger.exception("List my agents failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/agents")
async def create_agent(payload: CreateAgentRequest, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        metadata = NFTMetadata(
            encrypted_data_hash="0x" + "0" * 64,
            token_uri=payload.metadata_uri,
            royalty_receiver=user.wallet_address,
            royalty_bps=500,
        )
        session.add(metadata)
        await session.flush()

        agent = Agent(
            owner_id=user.user_id,
            creator_wallet=user.wallet_address or "",
            name=payload.name,
            persona=payload.persona,
            model_version=payload.model_version,
            metadata_uri=payload.metadata_uri,
            endpoint=payload.endpoint,
            capabilities=payload.capabilities,
            state=LifecycleState.CREATED,
        )
        agent.nft_metadata = metadata
        session.add(agent)
        await session.commit()
        await session.refresh(agent)

        return {
            "id": str(agent.id),
            "name": agent.name,
            "state": agent.state.value,
            "nft_id": agent.nft_id,
            "message": "Agent created successfully",
        }
    except Exception as exc:
        logger.exception("Create agent failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.put("/v1/agents/{agent_id}")
async def update_agent(agent_id: str, payload: UpdateAgentRequest, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        agent_uuid = __import__("uuid").UUID(agent_id)
        agent = await session.get(Agent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if agent.owner_id != user.user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        if payload.name is not None:
            agent.name = payload.name
        if payload.persona is not None:
            agent.persona = payload.persona
        if payload.model_version is not None:
            agent.model_version = payload.model_version
        if payload.metadata_uri is not None:
            agent.metadata_uri = payload.metadata_uri
        if payload.endpoint is not None:
            agent.endpoint = payload.endpoint
        if payload.capabilities is not None:
            agent.capabilities = payload.capabilities

        await session.commit()
        await session.refresh(agent)
        return {"id": str(agent.id), "name": agent.name, "state": agent.state.value}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Update agent failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.delete("/v1/agents/{agent_id}")
async def delete_agent(agent_id: str, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        agent_uuid = __import__("uuid").UUID(agent_id)
        agent = await session.get(Agent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if agent.owner_id != user.user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        agent.state = LifecycleState.ARCHIVED
        await session.commit()
        return {"status": "success", "message": "Agent archived"}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Delete agent failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Runtime / Chat Integration
# --------------------------------------------------------------------------

@app.post("/v1/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, user: SessionUser = Depends(get_current_user)):
    try:
        workflow_payload = {
            "agent_id": payload.agent_id,
            "message": payload.message,
            "metadata": {"user_id": user.user_id, "wallet": user.wallet_address},
        }
        result = await integration.execute_pipeline(payload.agent_id, payload.message, workflow_payload.get("metadata"))
        return ChatResponse(
            task_id=result["task_id"],
            status=result["status"],
            reply=result["message"],
            steps=result.get("steps", []),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Smart Contract Integration
# --------------------------------------------------------------------------

@app.post("/v1/contracts/transfer")
async def transfer_agent(payload: dict[str, Any], user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        to_address = payload.get("to_address")
        nft_id = payload.get("nft_id")
        if not to_address or nft_id is None:
            raise HTTPException(status_code=400, detail="to_address and nft_id required")

        result = await integration.proxy_request("smart-contract-integration", "/contracts/transfer", {
            "from_address": user.wallet_address or "",
            "to_address": to_address,
            "nft_id": nft_id,
        })
        return {"status": "success", "tx_hash": result.get("tx_hash")}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Transfer agent failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/contracts/rent")
async def rent_agent(payload: dict[str, Any], user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        nft_id = payload.get("nft_id")
        duration_days = payload.get("duration_days", 30)
        if nft_id is None:
            raise HTTPException(status_code=400, detail="nft_id required")

        result = await integration.proxy_request("smart-contract-integration", "/contracts/rent", {
            "nft_id": nft_id,
            "renter": user.wallet_address or "",
            "duration_days": duration_days,
        })
        return {"status": "success", "tx_hash": result.get("tx_hash")}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Rent agent failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Payment Flow
# --------------------------------------------------------------------------

@app.post("/v1/payments/crypto", response_model=PaymentResponse)
async def pay_crypto(payload: PaymentCryptoRequest, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        result = await integration.proxy_request("smart-contract-integration", "/contracts/buy", {
            "nft_id": payload.agent_id,
            "buyer": user.wallet_address or "",
            "price": payload.amount,
        })
        return PaymentResponse(status="success", tx_hash=result.get("tx_hash"), message="Crypto payment processed")
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Crypto payment failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/v1/payments/fiat", response_model=PaymentResponse)
async def pay_fiat(payload: PaymentFiatRequest, user: SessionUser = Depends(get_current_user)):
    try:
        return PaymentResponse(status="pending", tx_hash=None, message=f"Fiat payment initiated via {payload.provider}")
    except Exception as exc:
        logger.exception("Fiat payment failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --------------------------------------------------------------------------
# Frontend-compatible aliases (/v1/...)
# --------------------------------------------------------------------------

@app.get("/v1/auth/nonce")
def get_nonce_v1(address: str = Query("")):
    address = address.lower()
    if not address:
        raise HTTPException(status_code=400, detail="Missing address parameter")
    import secrets
    nonce = secrets.token_urlsafe(16)
    _nonces: dict[str, str] = {}
    _nonces[address] = nonce
    app.state.nonces = _nonces
    return {"nonce": nonce}


@app.post("/v1/auth/siwe")
async def login_siwe_v1(login: dict[str, str], session=Depends(get_db_session)):
    message = login.get("message", "")
    signature = login.get("signature", "")
    try:
        from eth_account.messages import encode_defunct
        from eth_account import Account
        encoded_message = encode_defunct(text=message)
        recovered_address = Account.recover_message(encoded_message, signature=signature)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid signature")

    nonces = getattr(app.state, "nonces", {})
    nonce = ""
    for line in message.split("\n"):
        if line.startswith("Nonce:"):
            nonce = line.split(":", 1)[1].strip()
            break
    if not nonce or nonces.get(recovered_address.lower()) != nonce:
        raise HTTPException(status_code=401, detail="Invalid or expired nonce")

    wallet = recovered_address.lower()
    stmt = select(User).where(User.wallet_address == wallet)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        user = User(wallet_address=wallet, role="user")
        session.add(user)
        await session.commit()
        await session.refresh(user)

    session_user = SessionUser(user_id=str(user.id), wallet_address=wallet, role=user.role)
    token = __import__("jwt", fromlist=["encode"]).encode(
        {"sub": session_user.user_id, "wallet": session_user.wallet_address, "role": session_user.role, "exp": __import__("time").time() + 86400},
        os.environ.get("JWT_SECRET", "dev-secret"),
        algorithm="HS256",
    )
    return {"access_token": token, "token_type": "bearer"}


@app.get("/v1/me")
def me_v1(user: SessionUser = Depends(get_current_user)):
    return user


@app.get("/v1/agents")
async def list_agents_v1(user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    stmt = select(Agent).where(Agent.owner_id == user.user_id).order_by(desc(Agent.created_at))
    result = await session.execute(stmt)
    agents = result.scalars().all()
    return {
        "agents": [
            {
                "id": str(a.id),
                "name": a.name,
                "state": a.state.value,
                "nft_id": a.nft_id,
                "persona": a.persona,
                "model_version": a.model_version,
                "metadata_uri": a.metadata_uri,
                "endpoint": a.endpoint,
                "token_bound_account": a.token_bound_account,
                "capabilities": a.capabilities,
                "creator_wallet": a.creator_wallet,
            }
            for a in agents
        ]
    }


@app.get("/v1/agents/{agent_id}")
async def get_agent_v1(agent_id: str, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    agent_uuid = __import__("uuid").UUID(agent_id)
    agent = await session.get(Agent, agent_uuid)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return {
        "id": str(agent.id),
        "name": agent.name,
        "state": agent.state.value,
        "nft_id": agent.nft_id,
        "persona": agent.persona,
        "model_version": agent.model_version,
        "metadata_uri": agent.metadata_uri,
        "endpoint": agent.endpoint,
        "token_bound_account": agent.token_bound_account,
        "capabilities": agent.capabilities,
        "creator_wallet": agent.creator_wallet,
    }


@app.get("/v1/marketplace/agents", response_model=list[MarketplaceAgent])
async def list_marketplace_agents_v1(
    user: SessionUser = Depends(get_current_user),
    session=Depends(get_db_session),
    q: str | None = Query(None, description="Search query"),
):
    return await list_marketplace(user=user, session=session, q=q)


@app.get("/v1/studio/agents")
async def list_studio_agents_v1(user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    result = await list_my_agents(user=user, session=session)
    return result.get("agents", [])


@app.post("/v1/studio/agents")
async def create_studio_agent_v1(payload: CreateAgentRequest, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    return await create_agent(payload=payload, user=user, session=session)


@app.patch("/v1/studio/agents/{agent_id}")
async def update_studio_agent_v1(agent_id: str, payload: UpdateAgentRequest, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    agent_uuid = __import__("uuid").UUID(agent_id)
    agent = await session.get(Agent, agent_uuid)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.owner_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    if payload.name is not None:
        agent.name = payload.name
    if payload.persona is not None:
        agent.persona = payload.persona
    if payload.model_version is not None:
        agent.model_version = payload.model_version
    if payload.metadata_uri is not None:
        agent.metadata_uri = payload.metadata_uri
    if payload.endpoint is not None:
        agent.endpoint = payload.endpoint
    if payload.capabilities is not None:
        agent.capabilities = payload.capabilities

    await session.commit()
    await session.refresh(agent)
    return {"id": str(agent.id), "name": agent.name, "state": agent.state.value}


@app.post("/v1/studio/agents/{agent_id}/publish")
async def publish_studio_agent_v1(agent_id: str, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    agent_uuid = __import__("uuid").UUID(agent_id)
    agent = await session.get(Agent, agent_uuid)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.owner_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    agent.state = LifecycleState.ACTIVE
    await session.commit()
    await session.refresh(agent)
    return {"id": str(agent.id), "name": agent.name, "state": agent.state.value}


@app.post("/v1/studio/agents/{agent_id}/unpublish")
async def unpublish_studio_agent_v1(agent_id: str, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    agent_uuid = __import__("uuid").UUID(agent_id)
    agent = await session.get(Agent, agent_uuid)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.owner_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized")

    agent.state = LifecycleState.ARCHIVED
    await session.commit()
    await session.refresh(agent)
    return {"id": str(agent.id), "name": agent.name, "state": agent.state.value}


@app.get("/v1/portfolio")
async def get_portfolio_v1(user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    stmt = select(Agent).where(Agent.owner_id == user.user_id, Agent.token_bound_account.isnot(None))
    result = await session.execute(stmt)
    agents = result.scalars().all()

    positions = []
    total_value_usd = 0.0
    for agent in agents:
        positions.append({
            "agent_name": agent.name,
            "asset": "ETH",
            "amount": 0.0,
            "value_usd": 0.0,
        })

    return {"total_value_usd": total_value_usd, "positions": positions}


@app.post("/v1/agents/{agent_id}/refresh")
async def refresh_agent(agent_id: str, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    try:
        agent_uuid = __import__("uuid").UUID(agent_id)
        agent = await session.get(Agent, agent_uuid)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if agent.owner_id != user.user_id:
            raise HTTPException(status_code=403, detail="Not authorized")

        agent.last_synced_at = __import__("datetime").datetime.utcnow()
        await session.commit()
        await session.refresh(agent)

        return {
            "task_id": str(__import__("uuid").uuid4()),
            "status_url": f"/v1/agents/{agent_id}",
            "agent": {
                "id": str(agent.id),
                "name": agent.name,
                "state": agent.state.value,
                "nft_id": agent.nft_id,
                "last_synced_at": agent.last_synced_at.isoformat() if agent.last_synced_at else None,
            },
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Refresh agent failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/v1/smart-contracts/transactions/{agent_id}")
async def get_smart_contract_transactions(agent_id: str, user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    agent_uuid = __import__("uuid").UUID(agent_id)
    stmt = select(Transaction).where(Transaction.agent_id == agent_uuid).order_by(desc(Transaction.created_at))
    result = await session.execute(stmt)
    txs = result.scalars().all()
    return [
        {
            "id": str(tx.id),
            "tx_hash": tx.tx_hash,
            "tx_type": tx.tx_type.value,
            "status": tx.status.value,
            "chain_id": tx.chain_id,
            "amount": float(tx.amount) if tx.amount else None,
            "confirmed_at": tx.confirmed_at.isoformat() if tx.confirmed_at else None,
        }
        for tx in txs
    ]


@app.post("/v1/smart-contracts/transfer")
async def smart_contract_transfer(payload: dict[str, Any], user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    return await transfer_agent(payload=payload, user=user, session=session)


@app.post("/v1/smart-contracts/list")
async def smart_contract_list(payload: dict[str, Any], user: SessionUser = Depends(get_current_user), session=Depends(get_db_session)):
    agent_id = payload.get("agent_id")
    price = payload.get("price_eth")
    if not agent_id or price is None:
        raise HTTPException(status_code=400, detail="agent_id and price_eth required")
    agent_uuid = __import__("uuid").UUID(agent_id)
    agent = await session.get(Agent, agent_uuid)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if agent.owner_id != user.user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return {"message": f"Agent listed for {price} ETH", "status": "success", "agent_id": agent_id}


@app.post("/v1/payments/intent")
async def create_payment_intent(payload: dict[str, Any], user: SessionUser = Depends(get_current_user)):
    intent_id = str(__import__("uuid").uuid4())
    return {"payment_id": intent_id, "message": "Payment intent created", "status": "pending"}


@app.post("/v1/payments/confirm")
async def confirm_payment(payload: dict[str, Any], user: SessionUser = Depends(get_current_user)):
    payment_id = payload.get("payment_id")
    tx_hash = payload.get("tx_hash")
    if not payment_id or not tx_hash:
        raise HTTPException(status_code=400, detail="payment_id and tx_hash required")
    return {"status": "confirmed", "message": "Payment confirmed", "payment_id": payment_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
