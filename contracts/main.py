import os, sys, time, json, logging, uuid, asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator, ConfigDict, SecretStr
from pydantic_settings import BaseSettings
from prometheus_client import Counter, Histogram, Gauge, generate_latest
from opentelemetry import trace
from redis.asyncio import Redis
import httpx, jwt

try:
    from slowapi import Limiter
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    HAS_SLOWAPI = True
except ImportError:
    HAS_SLOWAPI = False

sys.path.insert(0, os.path.dirname(__file__))  # Add contracts directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # Add parent directory
from .agent_graph import build_agent_graph, build_initial_state, PLANNER_NODE, MEMORY_NODE, SIMULATOR_NODE, EXECUTOR_NODE, CRITIC_NODE
from .state_enums import TaskStatus
from .state import AgentState
from .exceptions import PlanningPipelineError
from .planning_pipeline import Dependencies

# Exceptions & Logging
class ValidationError(Exception): pass
class AuthenticationError(Exception): pass
class AuthorizationError(Exception): pass
class WorkflowError(Exception): pass
class DatabaseError(Exception): pass
class BlockchainError(Exception): pass
class RateLimitError(Exception): pass
class ConfigurationError(Exception): pass
class AuthError(Exception): pass

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {"timestamp": datetime.utcnow().isoformat() + "Z", "level": record.levelname, "logger": record.name, 
             "message": record.getMessage(), "module": record.module, "function": record.funcName, "line": record.lineno}
        for attr in ["workflow_id", "task_id", "trace_id", "span_id", "user_id", "node_name", "latency_ms"]:
            if hasattr(record, attr): log_entry[attr] = getattr(record, attr)
        if record.exc_info and record.exc_info[0]: log_entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_entry)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logger.addHandler(handler)
tracer = trace.get_tracer(__name__)
class Environment(str, Enum):
    DEVELOPMENT, STAGING, PRODUCTION = "development", "staging", "production"

class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", extra="ignore")
    environment: Environment = Environment.DEVELOPMENT
    rpc_url: str = "https://mainnet.infura.io/v3/"
    db_url: str = "postgresql+asyncpg://user:pass@localhost/orch"
    redis_url: str = "redis://localhost:6379"
    jwt_secret: SecretStr = SecretStr("secret")
    rate_limit: str = "100/minute"
    timeout: int = 120
    max_retries: int = 3
    cors_origins: str = "*"
    api_keys: list = ["test-key"]
    allowed_hosts: list = ["*"]

    @field_validator("rpc_url", "db_url", "redis_url")
    @classmethod
    def validate_urls(cls, v, info):
        checks = {"rpc_url": ("http://", "https://"), "db_url": ("postgresql+asyncpg://", "sqlite+aiosqlite://"), "redis_url": ("redis://",)}
        if not v.startswith(checks.get(info.field_name, ("",))): raise ValueError(f"Invalid {info.field_name}")
        return v

settings = Settings()
REQUESTS = Counter("orchestrator_requests_total", "Total HTTP requests", ["endpoint", "status", "method"])
LATENCY = Histogram("orchestrator_latency_seconds", "Request latency", ["endpoint", "method"], buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60))
ACTIVE_WORKFLOWS = Gauge("orchestrator_active_workflows", "Currently running workflows")
WORKFLOW_DURATION = Histogram("orchestrator_workflow_duration_seconds", "Workflow duration", ["status"], buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120))

# Resilience & Repositories
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_threshold, self.recovery_timeout, self.failure_count, self.state = failure_threshold, recovery_timeout, 0, "closed"
        self.last_failure_time: Optional[float] = None
    def record_success(self): self.failure_count, self.state = 0, "closed"
    def record_failure(self):
        self.failure_count, self.last_failure_time = self.failure_count + 1, time.time()
        if self.failure_count >= self.failure_threshold: self.state = "open"
    def can_execute(self) -> bool:
        if self.state == "closed": return True
        return self.state == "open" and self.last_failure_time and (time.time() - self.last_failure_time >= self.recovery_timeout) and (setattr(self, "state", "half-open") or True)

async def with_retry(func, *args, max_retries: int = 3, base_delay: float = 1.0, circuit: Optional[CircuitBreaker] = None, **kwargs):
    if circuit and not circuit.can_execute(): raise BlockchainError("Circuit breaker open")
    for attempt in range(1, max_retries + 1):
        try: result = await func(*args, **kwargs); circuit and circuit.record_success(); return result
        except Exception as e:
            circuit and circuit.record_failure()
            if attempt == max_retries: raise
            await asyncio.sleep(base_delay * (2 ** (attempt - 1)))

class TaskRepository:
    async def get(self, task_id: str):
        from db.session import session_factory; import sqlalchemy as sa
        async with session_factory() as s:
            r = await s.execute(sa.text("SELECT * FROM agents WHERE id = :id"), {"id": task_id})
            row = r.fetchone()
            return dict(row._mapping) if row else None

    async def create(self, agent_id: str, user_request: str, priority: str = "normal") -> str:
        from db.session import session_factory; from db.models.agents import Agent, LifecycleState
        task_id = str(uuid.uuid4())
        async with session_factory() as s: s.add(Agent(id=task_id, user_request=user_request, priority=priority, state=LifecycleState.CREATED)); await s.commit()
        return task_id
    
    async def list_tasks(self, limit: int = 20, offset: int = 0):
        from db.session import session_factory; import sqlalchemy as sa
        async with session_factory() as s:
            r = await s.execute(sa.text("SELECT id, user_request, priority, state, created_at FROM agents ORDER BY created_at DESC LIMIT :limit OFFSET :offset"), {"limit": limit, "offset": offset})
            return [dict(row._mapping) for row in r.fetchall()]
    
    async def delete(self, task_id: str) -> bool:
        from db.session import session_factory; import sqlalchemy as sa
        async with session_factory() as s:
            r = await s.execute(sa.text("DELETE FROM agents WHERE id=:id"), {"id": task_id})
            await s.commit()
            return r.rowcount > 0

class ExecutionRepository:
    async def save_state(self, task_id: str, state: AgentState, node_name: str, step_status):
        from db.session import session_factory; from db.models.execution_history import ExecutionStep, AgentRole
        async with session_factory() as s: s.add(ExecutionStep(task_id=uuid.UUID(task_id), agent_id=uuid.UUID(state["agent_id"]), role=AgentRole(node_name), status=step_status, sequence=state.get("current_subtask_index", 0), output_summary=json.dumps({"status": state["status"], "subtasks": len(state.get("subtasks", []))}))); await s.commit()

    async def get_history(self, task_id: str):
        from db.session import session_factory; import sqlalchemy as sa
        async with session_factory() as s:
            r = await s.execute(sa.text("SELECT role, status, output_summary, created_at FROM execution_history WHERE task_id = :id ORDER BY sequence"), {"id": task_id})
            return [dict(row._mapping) for row in r.fetchall()]

class WalletRepository:
    async def validate(self, address: str) -> bool:
        if not address.startswith("0x") or len(address) != 42: return False
        try: 
            int(address, 16)
            from eth_keys.utils.address import is_checksum_valid
            return is_checksum_valid(address) if address != address.lower() and address != address.upper() else True
        except: return False

class AuditRepository:
    async def log(self, task_id: str, user_id: str, wallet: str, action: str, tx_hash: Optional[str] = None, gas_used: Optional[int] = None):
        from db.session import session_factory; from db.models.execution_history import ExecutionStep, AgentRole, StepStatus
        async with session_factory() as s: s.add(ExecutionStep(task_id=uuid.UUID(task_id), agent_id=uuid.UUID(user_id), role=AgentRole.AUDIT, status=StepStatus.SUCCEEDED, sequence=0, output_summary=json.dumps({"action": action, "wallet": wallet, "tx_hash": tx_hash, "gas_used": gas_used, "timestamp": datetime.utcnow().isoformat()}))); await s.commit()

class OrchestratorService:
    def __init__(self, workflow, repositories):
        self.workflow, self.repos = workflow, repositories
        self.node_latencies = {}
    
    async def execute(self, initial_state: AgentState):
        from db.models.execution_history import StepStatus
        workflow_id, start = initial_state["task_id"], time.time()
        try:
            result = await self.workflow.ainvoke(initial_state)
            duration = time.time() - start
            WORKFLOW_DURATION.labels(status=result.get("status", "unknown")).observe(duration)
            
            # Measure individual node latencies
            for node in [PLANNER_NODE, MEMORY_NODE, SIMULATOR_NODE, EXECUTOR_NODE, CRITIC_NODE]:
                if f"{node}_latency_ms" in result:
                    node_start = time.time() - (duration - result[f"{node}_latency_ms"] / 1000)
                    self.node_latencies[node] = time.time() - node_start
            
            await self.repos["execution"].save_state(workflow_id, result, "workflow", StepStatus.SUCCEEDED)
            if result.get("execution", {}).get("tx_hash"):
                await self.repos["audit"].log(workflow_id, result["agent_id"], result.get("wallet", ""), "workflow_execution", result["execution"]["tx_hash"], result["execution"].get("actual_gas_used"))
            return result
        except Exception as e:
            WORKFLOW_DURATION.labels(status="failed").observe(time.time() - start)
            failed_state = {**initial_state, "status": TaskStatus.FAILED, "critique": {"outcome_matches_intent": False, "should_retry": False, "feedback": str(e)}}
            await self.repos["execution"].save_state(workflow_id, failed_state, "workflow", StepStatus.FAILED)
            raise WorkflowError(f"Pipeline error: {e}" if isinstance(e, PlanningPipelineError) else f"Workflow failure: {e}")

class Container:
    def __init__(self):
        self._db = self._redis = self._http = self._workflow = None
        self._repositories = {}
    async def init(self):
        try: from db.session import session_factory; self._db = session_factory
        except ImportError: logger.warning("Database unavailable"); self._db = None
        self._redis, self._http = Redis.from_url(settings.redis_url, decode_responses=True), httpx.AsyncClient(timeout=settings.timeout)
        self._repositories = {"task": TaskRepository(), "execution": ExecutionRepository(), "wallet": WalletRepository(), "audit": AuditRepository()}
        self._workflow = build_agent_graph(deps=Dependencies(llm_client=None, vector_store=None, db_session_factory=self._db, portfolio_client=None, chain_client=None, policy_engine_client=None, agent_registry_client=None, tool_router=None))
    async def shutdown(self):
        if self._redis: await self._redis.close()
        if self._http: await self._http.aclose()
    @property
    def workflow(self): return self._workflow
    @property
    def redis(self): return self._redis
    @property
    def repositories(self): return self._repositories

container = Container()

async def auth(request: Request):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not token: raise HTTPException(status_code=401, detail="Missing token")
    try: payload = jwt.decode(token, settings.jwt_secret.get_secret_value(), algorithms=["HS256"]); return {"user": payload.get("sub"), "role": payload.get("role", "user"), "scopes": payload.get("scopes", [])}
    except jwt.ExpiredSignatureError: raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError: raise HTTPException(status_code=401, detail="Invalid token")

class ExecuteRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"agent_id": "agent-1", "instruction": "Swap 1 ETH to USDC"}]})
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = Field(..., min_length=1, max_length=100)
    instruction: str = Field(..., min_length=1, max_length=10000)
    wallet: Optional[str] = None
    policy: Optional[Dict] = None
    priority: str = Field(default="normal", pattern="^(low|normal|high|critical)$")
    @field_validator("wallet")
    @classmethod
    def validate_wallet(cls, v):
        if v and (not v.startswith("0x") or len(v) != 42 or not all(c in "0123456789abcdefABCDEF" for c in v[2:])): raise ValueError("Invalid wallet")
        return v

class ExecuteResponse(BaseModel):
    task_id: str
    status: TaskStatus
    result: Optional[Dict] = None
    duration: float
    node_latencies: Dict = Field(default_factory=dict)
    version: str = "1.0.0"

class HealthResponse(BaseModel):
    status: str; redis: bool; db: bool; blockchain_rpc: bool; timestamp: datetime = Field(default_factory=datetime.utcnow)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await container.init(); logger.info("Started"); yield; await container.shutdown(); logger.info("Stopped")

app = FastAPI(title="Agent Orchestrator", description="Agentic AI workflow orchestrator", version="1.0.0", 
    lifespan=lifespan, docs_url="/api/v1/docs", redoc_url="/api/v1/redoc", openapi_url="/api/v1/openapi.json")

limiter = Limiter(key_func=get_remote_address) if HAS_SLOWAPI else type("L", (), {"limit": lambda *a, **kw: lambda f: f})()
app.state.limiter = limiter
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(",") if settings.cors_origins != "*" else ["*"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"], max_age=600)

@app.middleware("http")
async def _middleware(request: Request, call_next):
    request.state.correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.trace_id = request.state.correlation_id
    size = request.headers.get("content-length")
    if size and int(size) > 10485760: raise HTTPException(status_code=413, detail="Payload too large")
    start = time.time()
    try:
        response = await call_next(request)
        LATENCY.labels(endpoint=request.url.path, method=request.method).observe(time.time() - start)
        response.headers.update({"X-Correlation-ID": request.state.correlation_id, "X-Trace-ID": request.state.trace_id,
            "Strict-Transport-Security": "max-age=31536000", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff"})
        if settings.environment == Environment.PRODUCTION: response.headers["Content-Security-Policy"] = "default-src 'self'"
        return response
    except Exception: LATENCY.labels(endpoint=request.url.path, method=request.method).observe(time.time() - start); raise

async def check_blockchain_rpc() -> bool:
    try: r = await container._http.get(settings.rpc_url, timeout=5.0); return r.status_code < 500
    except: return False

@app.get("/")
async def root(): return {"message": "Agent Orchestrator API", "docs": "/api/v1/docs", "status": "running"}

@app.get("/health/liveness")
async def liveness(): return {"status": "alive", "timestamp": datetime.utcnow().isoformat() + "Z"}

@app.get("/health/readiness", response_model=HealthResponse)
async def readiness():
    checks = {"redis": False, "database": False, "blockchain_rpc": False, "llm": False, "vector_store": False, "policy_engine": False}
    try: await container.redis.ping(); checks["redis"] = True
    except: pass
    try: from db.database import check_health; checks["database"] = await check_health()
    except: pass
    try: checks["blockchain_rpc"] = await check_blockchain_rpc()
    except: pass
    try:
        import openai
        openai.api_key = os.getenv("OPENAI_API_KEY", "")
        checks["llm"] = bool(openai.api_key)
    except: pass
    try:
        from vector_store import get_client
        vs = get_client()
        await vs.health_check()
        checks["vector_store"] = True
    except: pass
    try:
        async with container._http.get(f"{os.getenv('POLICY_ENGINE_URL', 'http://localhost:9000')}/health", timeout=5.0) as resp:
            checks["policy_engine"] = resp.status < 500
    except: pass
    if not all(checks.values()): raise HTTPException(status_code=503, detail={"checks": checks})
    return HealthResponse(status="ready", redis=checks["redis"], db=checks["database"], blockchain_rpc=checks["blockchain_rpc"])

@app.get("/health")
async def health(): return {"status": "healthy", "version": "1.0.0", "environment": settings.environment}

@app.get("/api/v1/metrics")
async def metrics(): return Response(generate_latest(), media_type="text/plain")

@app.get("/api/v1/status/{task_id}", tags=["Workflow"])
async def get_status(task_id: str, user: dict = Depends(auth)):
    h = await container.repositories["execution"].get_history(task_id)
    if not h: raise HTTPException(status_code=404, detail="Not found")
    return {"task_id": task_id, "steps": h, "latest_status": h[-1]["status"] if h else None}

@app.get("/api/v1/workflows", tags=["Workflow"])
async def list_workflows(limit: int = 20, offset: int = 0, user: dict = Depends(auth)):
    workflows = await container.repositories["task"].list_tasks(limit=limit, offset=offset)
    return {"count": len(workflows), "items": workflows}

@app.post("/api/v1/workflows/{task_id}/retry", tags=["Workflow"])
async def retry_workflow(task_id: str, user: dict = Depends(auth)):
    task = await container.repositories["task"].get(task_id)
    if not task: raise HTTPException(status_code=404, detail="Workflow not found")
    state = build_initial_state(task_id=task_id, agent_id=task["id"], user_request=task["user_request"], max_retries=settings.max_retries)
    service = OrchestratorService(workflow=container.workflow, repositories=container.repositories)
    result = await service.execute(state)
    return result

@app.delete("/api/v1/workflows/{task_id}", tags=["Workflow"])
async def delete_workflow(task_id: str, user: dict = Depends(auth)):
    deleted = await container.repositories["task"].delete(task_id)
    if not deleted: raise HTTPException(status_code=404, detail="Workflow not found")
    return {"message": "Workflow deleted successfully"}

@app.get("/api/v1/workflows/{task_id}/audit", tags=["Workflow"])
async def audit(task_id: str, user: dict = Depends(auth)):
    history = await container.repositories["execution"].get_history(task_id)
    return {"task_id": task_id, "audit": history}

@app.get("/api/v1/workflows/{task_id}/metrics", tags=["Workflow"])
async def workflow_metrics(task_id: str, user: dict = Depends(auth)):
    history = await container.repositories["execution"].get_history(task_id)
    return {"task_id": task_id, "steps": len(history), "history": history}

@app.post("/api/v1/execute", response_model=ExecuteResponse, summary="Execute workflow", tags=["Workflow"])
@limiter.limit(settings.rate_limit)
async def execute(request: ExecuteRequest, req: Request, user: dict = Depends(auth)):
    with tracer.start_as_current_span("workflow.execute") as span:
        span.set_attribute("task_id", request.task_id); span.set_attribute("agent_id", request.agent_id)
        ACTIVE_WORKFLOWS.inc(); REQUESTS.labels(endpoint="/api/v1/execute", status="attempt", method="POST").inc()
        try:
            if user["role"] != "admin" and user.get("agent_id") != request.agent_id: raise AuthorizationError("Unauthorized")
            if request.wallet and not await container.repositories["wallet"].validate(request.wallet): raise ValidationError("Invalid wallet")
            state = build_initial_state(task_id=request.task_id, agent_id=request.agent_id, user_request=request.instruction, max_retries=settings.max_retries)
            state.update({"wallet": request.wallet, "policy": request.policy or {}, "priority": request.priority})
            svc = OrchestratorService(workflow=container.workflow, repositories=container.repositories)
            start = time.time()
            result = await svc.execute(state)
            latencies = {n: time.time() - start for n in [PLANNER_NODE, MEMORY_NODE, SIMULATOR_NODE, EXECUTOR_NODE, CRITIC_NODE]}
            REQUESTS.labels(endpoint="/api/v1/execute", status="success", method="POST").inc()
            return ExecuteResponse(task_id=request.task_id, status=result.get("status", TaskStatus.FAILED), result=result.get("result"), duration=time.time() - start, node_latencies=latencies)
        except AuthorizationError: REQUESTS.labels(endpoint="/api/v1/execute", status="authorization_error", method="POST").inc(); raise HTTPException(status_code=403, detail="Forbidden")
        except ValidationError: REQUESTS.labels(endpoint="/api/v1/execute", status="validation_error", method="POST").inc(); raise HTTPException(status_code=422, detail="Invalid")
        except WorkflowError: REQUESTS.labels(endpoint="/api/v1/execute", status="workflow_error", method="POST").inc(); raise HTTPException(status_code=500, detail="Workflow error")
        except HTTPException: raise
        except Exception as e: REQUESTS.labels(endpoint="/api/v1/execute", status="internal_error", method="POST").inc(); logger.exception("Error"); raise HTTPException(status_code=500, detail="Internal error")
        finally: ACTIVE_WORKFLOWS.dec()

@app.exception_handler(Exception)
async def generic_error(request: Request, exc: Exception):
    req_id = getattr(request.state, "correlation_id", "unknown")
    logger.exception("Unhandled exception")
    return JSONResponse(status_code=500, content={"error": "Server error", "req_id": req_id})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8002")), log_config=None)
