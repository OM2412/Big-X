# main.py
#
# Entry point for the agent-orchestrator service. Exposes /agent/message —
# the endpoint your ChatInterface.tsx component already calls — which runs
# a user request through the full Planner -> Memory -> Simulator ->
# Executor -> Critic graph and returns a summary of what happened.

import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agents.planner import PlannerAgent
from .agents.memory_knowledge import MemoryKnowledgeAgent
from .agents.simulator import SimulatorAgent
from .agents.executor import ExecutorAgent
from .agents.critic import CriticAgent
from .graph.langgraph_flow import build_agent_graph, build_initial_state

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Orchestrator")

# --------------------------------------------------------------------------
# Dependency wiring — replace these stubs with your real clients at startup.
# Kept as module-level placeholders so this file shows the wiring shape
# without guessing at your LLM provider / RPC setup / vector store choice.
# --------------------------------------------------------------------------

llm_client = None            # TODO: your Claude/GPT API wrapper
vector_store = None          # TODO: pgvector/Pinecone client
db_session_factory = None    # TODO: SQLAlchemy async session factory
portfolio_client = None      # TODO: reads on-chain balances for an agent
chain_client = None          # TODO: web3/RPC wrapper
policy_engine_client = None  # TODO: calls PolicyEngine.sol
tool_router = None           # TODO: services/tool_router client

_graph = None  # compiled once at startup, reused across requests


class _LocalLLM:
    async def complete(self, *, system: str, user: str, context: dict) -> str:
        return '[{"tool": "price_feed_tool", "action": "inspect", "params": {"query": "' + user.replace('"', '') + '"}}]'


class _LocalPortfolio:
    async def get_portfolio(self, agent_id: str) -> dict:
        return {"agent_id": agent_id, "balances": []}


class _LocalVectorStore:
    async def embed(self, query: str) -> list[float]:
        return []

    async def query(self, embedding: list[float], top_k: int = 5) -> list:
        return []


class _LocalSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _LocalChain:
    async def estimate_gas(self, *, to: str, value: int, data: bytes) -> int:
        return 0

    async def wait_for_receipt(self, tx_hash: str):
        return type("Receipt", (), {"success": True, "gas_used": 0})()


class _LocalPolicy:
    async def check_action(self, *, agent_id: str, target: str, value: int, data: bytes) -> tuple[bool, str]:
        return True, "Allowed in local simulation mode"

    async def record_spend(self, *, agent_id: str, value: float) -> None:
        return None


class _LocalRouter:
    async def dispatch(self, *, agent_id: str, tool: str, action: str, params: dict) -> str:
        return "0xlocal-simulation"


def _local_mode_enabled() -> bool:
    return os.getenv("AGENT_ORCHESTRATOR_MODE", "local").lower() == "local"


@app.on_event("startup")
def wire_agents():
    global _graph
    if _local_mode_enabled():
        local_llm = _LocalLLM()
        local_vector_store = _LocalVectorStore()
        local_session_factory = lambda: _LocalSession()
        local_portfolio = _LocalPortfolio()
        local_chain = _LocalChain()
        local_policy = _LocalPolicy()
        local_router = _LocalRouter()
        planner_client = local_llm
        memory_client = (local_vector_store, local_session_factory, local_portfolio)
        simulator_clients = (local_chain, local_policy)
        executor_clients = (local_router, local_chain, local_policy, local_session_factory)
        critic_clients = (local_llm, local_session_factory)
    else:
        planner_client = llm_client
        memory_client = (vector_store, db_session_factory, portfolio_client)
        simulator_clients = (chain_client, policy_engine_client)
        executor_clients = (tool_router, chain_client, policy_engine_client, db_session_factory)
        critic_clients = (llm_client, db_session_factory)

    planner = PlannerAgent(planner_client)
    memory = MemoryKnowledgeAgent(*memory_client)
    simulator = SimulatorAgent(*simulator_clients, price_oracle=None)
    executor = ExecutorAgent(*executor_clients)
    critic = CriticAgent(*critic_clients)

    _graph = build_agent_graph(planner, memory, simulator, executor, critic)
    logger.info("Agent graph compiled and ready")


class MessageRequest(BaseModel):
    agent_id: str
    message: str


class MessageResponse(BaseModel):
    task_id: str
    status: str
    reply: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/agent/message", response_model=MessageResponse)
async def handle_message(req: MessageRequest):
    if _graph is None:
        raise HTTPException(status_code=503, detail="Agent graph not ready")

    task_id = str(uuid.uuid4())
    initial_state = build_initial_state(task_id, req.agent_id, req.message)

    try:
        final_state = await _graph.ainvoke(initial_state)
    except Exception:
        logger.exception("Task %s failed unexpectedly", task_id)
        raise HTTPException(status_code=500, detail="Agent task failed")

    reply = _summarize(final_state)
    return MessageResponse(task_id=task_id, status=final_state["status"], reply=reply)


def _summarize(state: dict) -> str:
    if state["status"] == "done":
        return f"Done — completed {len(state['subtasks'])} step(s) successfully."
    if state["status"] == "failed":
        critique = state.get("critique") or {}
        return f"Couldn't complete this: {critique.get('feedback', 'unknown error')}"
    return f"Task is still {state['status']}."