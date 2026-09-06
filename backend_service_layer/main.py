from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env.example", override=True)

import logging
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "services"))
sys.path.insert(0, str(REPO_ROOT / "services" / "shared" / "src"))

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.base import BaseHTTPMiddleware

from db import wait_for_database, init_db_schema, close_db
from db.models.users import User
from db.session import get_db_session, get_session

# Logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

# Rate limiting

limiter = Limiter(key_func=get_remote_address, storage_uri="memory://", default_limits=["200/minute"])
AUTH_RATE_LIMIT = "10/minute"
REFRESH_RATE_LIMIT = "20/minute"

# Auth

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY = 60 * 60 * 24

class SessionUser(BaseModel):
    user_id: str
    wallet_address: str | None = None
    role: str = "user"

class SiweLoginRequest(BaseModel):
    message: str
    signature: str

class NonceResponse(BaseModel):
    nonce: str

_nonces: dict[str, str] = {}

def generate_nonce() -> str:
    import secrets
    return secrets.token_urlsafe(16)

def store_nonce(address: str, nonce: str) -> None:
    _nonces[address.lower()] = nonce

def verify_and_consume_nonce(address: str, nonce: str) -> bool:
    stored = _nonces.get(address.lower())
    if not stored or stored != nonce:
        return False
    del _nonces[address.lower()]
    return True

def create_session_token(user: SessionUser) -> str:
    import jwt as jwt_module
    payload = {"sub": user.user_id, "wallet": user.wallet_address, "role": user.role, "exp": time.time() + JWT_EXPIRY}
    return jwt_module.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()

def verify_siwe(login: SiweLoginRequest) -> tuple[str, str]:
    from eth_account.messages import encode_defunct
    from eth_account import Account
    try:
        encoded_message = encode_defunct(text=login.message)
        recovered_address = Account.recover_message(encoded_message, signature=login.signature)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    nonce = ""
    for line in login.message.split("\n"):
        if line.startswith("Nonce:"):
            nonce = line.split(":", 1)[1].strip()
            break
    
    if not nonce:
        raise HTTPException(status_code=400, detail="Missing nonce in SIWE message")
    
    if not verify_and_consume_nonce(recovered_address, nonce):
        raise HTTPException(status_code=401, detail="Invalid or expired nonce")
    
    return recovered_address, nonce

def get_current_user(request: Request) -> SessionUser:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth.split(" ", 1)[1]
    import jwt as jwt_module
    try:
        payload = jwt_module.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt_module.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt_module.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    return SessionUser(user_id=payload["sub"], wallet_address=payload.get("wallet"), role=payload.get("role", "user"))

def current_user(request: Request) -> SessionUser:
    return get_current_user(request)

# Dependencies

async def db_session() -> AsyncGenerator:
    async with get_session() as session:
        yield session

# Middleware

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id
        start = time.monotonic()
        logger.info("Request started", extra={"context": {"correlation_id": correlation_id, "method": request.method, "path": request.url.path}})
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.monotonic() - start) * 1000
            logger.exception("Request failed", extra={"context": {"correlation_id": correlation_id, "method": request.method, "path": request.url.path, "duration_ms": round(duration_ms, 1)}})
            raise
        duration_ms = (time.monotonic() - start) * 1000
        logger.info("Request completed", extra={"context": {"correlation_id": correlation_id, "method": request.method, "path": request.url.path, "status_code": response.status_code, "duration_ms": round(duration_ms, 1)}})
        response.headers["X-Correlation-ID"] = correlation_id
        return response

# Exception handlers

from services.shared.src.blockchain_client import (
    BlockchainClientError, ContractNotDeployedError, ProviderUnavailableError,
    ContractCallError, ContractWriteError, TransactionRevertedError,
    NonceError, InputValidationError,
    NonceManager, ContractCache, MetricsCollector,
)
from services.wallet_service.src.rpc_provider_manager import RpcProviderManager, ProviderConfig
from services.shared.src.agent_registry_client import AgentRegistryClient, AgentRegistryConfig
from .agent_service import AgentService, AgentNotFoundError

_STATUS_MAP = {
    AgentNotFoundError: (404, True),
    InputValidationError: (400, True),
    ContractNotDeployedError: (503, True),
    ProviderUnavailableError: (503, True),
    TransactionRevertedError: (502, True),
    ContractCallError: (502, False),
    ContractWriteError: (502, False),
    NonceError: (502, False),
}

def _error_response(request: Request, error_id: str, status_code: int, message: str):
    return JSONResponse(status_code=status_code, content={"error_id": error_id, "message": message, "path": str(request.url.path)})

def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BlockchainClientError)
    @app.exception_handler(AgentNotFoundError)
    async def handle_known_errors(request: Request, exc: Exception):
        error_id = str(uuid.uuid4())
        status_code, expose_message = _STATUS_MAP.get(type(exc), (500, False))
        logger.error("Handled exception: %s", exc, extra={"context": {"error_id": error_id, "error_type": type(exc).__name__, "path": request.url.path}})
        message = str(exc) if expose_message else "An internal error occurred while processing this request."
        return _error_response(request, error_id, status_code, message)

    @app.exception_handler(Exception)
    async def handle_unexpected_errors(request: Request, exc: Exception):
        error_id = str(uuid.uuid4())
        logger.exception("Unhandled exception, error_id=%s", error_id)
        return _error_response(request, error_id, 500, "An unexpected error occurred. Reference this error_id if reporting the issue.")

# Rate limit handler

async def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Rate limit exceeded", "detail": "Too many requests. Please try again later."})

# Services

from .portfolio_service import PortfolioService

# Routers

router_v1 = APIRouter(prefix="/v1", tags=["api"])

@router_v1.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}

@router_v1.get("/rpc-status")
async def rpc_status(request: Request):
    return request.app.state.rpc_manager.get_status()

@router_v1.get("/auth/nonce")
def get_nonce(request: Request):
    address = request.query_params.get("address", "").lower()
    if not address:
        raise HTTPException(status_code=400, detail="Missing address parameter")
    nonce = generate_nonce()
    store_nonce(address, nonce)
    return {"nonce": nonce}

@router_v1.post("/auth/siwe")
@limiter.limit(AUTH_RATE_LIMIT)
async def login(request: Request, login: SiweLoginRequest, session = Depends(get_db_session)):
    wallet_address, nonce = verify_siwe(login)
    
    from sqlalchemy import select
    result = await session.execute(select(User).where(User.wallet_address == wallet_address.lower()))
    user = result.scalar_one_or_none()
    if not user:
        user = User(wallet_address=wallet_address.lower(), role="user")
        session.add(user)
        await session.commit()
        await session.refresh(user)
    
    session_user = SessionUser(user_id=str(user.id), wallet_address=user.wallet_address, role=user.role)
    token = create_session_token(session_user)
    return {"access_token": token, "token_type": "bearer"}

@router_v1.get("/me")
def me(user: SessionUser = Depends(current_user)):
    return user

# Portfolio

class PositionResponse(BaseModel):
    agent_name: str
    asset: str
    amount: float
    value_usd: float

class PortfolioResponse(BaseModel):
    total_value_usd: float
    positions: list[PositionResponse]

def get_portfolio_service(request: Request) -> PortfolioService:
    return request.app.state.portfolio_service

@router_v1.get("/portfolio", response_model=PortfolioResponse, tags=["portfolio"])
async def get_portfolio(
    request: Request,
    user: SessionUser = Depends(current_user),
    session = Depends(db_session),
):
    portfolio = await request.app.state.portfolio_service.get_for_user(session, user.user_id)
    return PortfolioResponse(
        total_value_usd=portfolio.total_value_usd,
        positions=[
            PositionResponse(
                agent_name=p.agent_name,
                asset=p.asset,
                amount=p.amount,
                value_usd=p.value_usd,
            )
            for p in portfolio.positions
        ],
    )

# Agent routes

agents_router = APIRouter(prefix="/agents", tags=["agents"])

@agents_router.get("")
async def list_agents(
    request: Request,
    user: SessionUser = Depends(current_user),
):
    agent_service = request.app.state.agent_service
    async with get_session() as session:
        agents = await agent_service.list_for_user(session, user.user_id)
    return {
        "agents": [
            {
                "id": str(a.id),
                "name": a.name,
            }
            for a in agents
        ]
    }

router_v1.include_router(agents_router)

# FastAPI app

required_env = ["CHAIN_RPC_URL", "REGISTRAR_PRIVATE_KEY"]
missing = [key for key in required_env if not os.environ.get(key)]
if missing:
    raise RuntimeError(f"Missing required environment variable(s): {', '.join(missing)}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("API gateway starting up...")
    await wait_for_database()
    await init_db_schema()

    rpc_manager = RpcProviderManager(
        chain_id=int(os.environ.get("CHAIN_ID", "8453")),
        providers=[ProviderConfig(name="primary", rpc_url=os.environ["CHAIN_RPC_URL"], priority=0, chain_id=int(os.environ.get("CHAIN_ID", "8453")))],
    )
    rpc_manager.start_health_checks()

    shared_nonces, shared_contracts, shared_metrics = NonceManager(), ContractCache(), MetricsCollector()

    agent_registry_client = AgentRegistryClient(
        config=AgentRegistryConfig(operator_private_key=os.environ["REGISTRAR_PRIVATE_KEY"], network=os.environ.get("NETWORK", "base")),
        rpc_provider_manager=rpc_manager,
        nonce_manager=shared_nonces, contract_cache=shared_contracts, metrics=shared_metrics,
    )

    from services.tool_router.src.tools.wallet_tool import WalletTool
    from services.tool_router.src.tools.price_feed_tool import PriceFeedTool
    from services.oracle_service.src.chainlink_client import ChainlinkClient
    from services.oracle_service.src.pyth_client import PythClient
    from services.oracle_service.src.redstone_client import RedstoneClient

    wallet_tool = WalletTool(w3=rpc_manager.get_provider())
    price_feed_tool = PriceFeedTool()

    app.state.rpc_manager = rpc_manager
    app.state.agent_service = AgentService(agent_registry_client=agent_registry_client)
    app.state.portfolio_service = PortfolioService(wallet_tool=wallet_tool, price_feed_tool=price_feed_tool)

    logger.info("API gateway ready")
    yield

    logger.info("API gateway shutting down...")
    rpc_manager.stop_health_checks()
    await close_db()


app = FastAPI(title="Agentic DeFi Platform — API Gateway", version="1.0.0", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
register_exception_handlers(app)
app.add_middleware(RequestLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.environ.get("WEB_APP_ORIGIN", "http://localhost:5173"),
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .routers.marketplace import router as marketplace_router
from .routers.smart_contracts import router as smart_contracts_router
from .routers.payments import router as payments_router
from .routers.studio import router as studio_router

router_v1.include_router(marketplace_router)
router_v1.include_router(smart_contracts_router)
router_v1.include_router(payments_router)
router_v1.include_router(studio_router)

app.include_router(router_v1)

@app.get("/")
async def root():
    return {"service": "backend_service_layer", "docs": "/docs", "health": "/v1/health"}

@app.get("/health")
async def health_root():
    return {"status": "ok"}
