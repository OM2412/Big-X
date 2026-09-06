import time
import logging
import uuid
from dataclasses import dataclass
from typing import Protocol

from pydantic import ValidationError

from state import AgentState, SubTask, SimulationResult
from schemas import PlannerOutputModel
from exceptions import (
    PlannerParseError, CapabilityDeniedError, MemoryRetrievalError,
    ToolResolutionError, PolicyRejectedError, TransientDependencyError,
)
from resilience import traced_span, with_retry, CircuitBreaker, TTLCache
from state_enums import TaskStatus

logger = logging.getLogger(__name__)

AVAILABLE_TOOLS = [
    "swap_tool", "bridge_tool", "defi_tool", "nft_tool", "wallet_tool", "price_feed_tool",
]

# Maps a tool to the on-chain capability bit it requires — kept in sync
# with CapabilityRegistry.sol's bit assignments.
TOOL_REQUIRED_CAPABILITY = {
    "swap_tool": "CAP_SWAP",
    "bridge_tool": "CAP_BRIDGE",
    "defi_tool": "CAP_YIELD_FARM",
    "nft_tool": "CAP_NFT_TRADE",
    "wallet_tool": None,       # balance reads/transfers — no gated capability
    "price_feed_tool": None,   # read-only
}

PLANNER_SYSTEM_PROMPT = """You are a planning agent for an on-chain execution system.
Break the user's request into an ordered list of subtasks. Each subtask must
use one of these tools: {tools}.

Respond ONLY as a JSON object: {{"subtasks": [{{"tool": "...", "action": "...", "params": {{...}}}}]}}
"""


# ============================================================================
# Dependency injection container
# ============================================================================

class LlmClient(Protocol):
    async def complete(self, system: str, user: str, context: dict) -> str: ...


class Dependencies:
    """Constructed once at service startup (agent-orchestrator/src/main.py)
    and passed into all three agents below. Add a new dependency here,
    once, rather than threading it through three constructors."""

    def __init__(
        self, llm_client: LlmClient, vector_store, db_session_factory,
        portfolio_client, chain_client, policy_engine_client,
        agent_registry_client, tool_router,
    ):
        self.llm_client = llm_client
        self.vector_store = vector_store
        self.db_session_factory = db_session_factory
        self.portfolio_client = portfolio_client
        self.chain_client = chain_client
        self.policy_engine_client = policy_engine_client
        self.agent_registry_client = agent_registry_client  # real client from earlier: agent_registry.py
        self.tool_router = tool_router                        # real client from earlier: tool-router/router.py

        self.policy_circuit = CircuitBreaker()
        self.chain_circuit = CircuitBreaker()
        self.capability_cache = TTLCache(ttl_seconds=60.0)  # capability bitmask rarely changes mid-task

async def _log_step(deps: Dependencies, task_id: str, agent_id: str, role: str, status: str, detail: str | None = None) -> None:
    try:
        from db.models.execution_history import ExecutionStep, AgentRole, StepStatus
        async with deps.db_session_factory() as session:
            step = ExecutionStep(
                id=str(uuid.uuid4()), task_id=task_id, agent_id=agent_id,
                role=AgentRole(role), status=StepStatus(status),
                sequence=0, output_summary=detail,
            )
            session.add(step)
            await session.commit()
    except Exception:
        # Never let audit logging failures break the actual pipeline —
        # log loudly so it's visible in monitoring, same rule as nft_tool.py.
        logger.exception("planning_pipeline: failed to write execution_history entry")

class PlannerAgent:
    def __init__(self, deps: Dependencies):
        self.deps = deps

    async def run(self, state: AgentState) -> AgentState:
        with traced_span("planner.run", task_id=state["task_id"], agent_id=state["agent_id"]):
            logger.info("Planner: breaking down request for task %s", state["task_id"], extra={"task_id": state["task_id"]})

            prompt = PLANNER_SYSTEM_PROMPT.format(tools=", ".join(AVAILABLE_TOOLS))

            try:
                response = await with_retry(
                    self.deps.llm_client.complete,
                    system=prompt, user=state["user_request"], context=state.get("context", {}),
                    max_retries=3,
                )
            except TransientDependencyError:
                await _log_step(self.deps, state["task_id"], state["agent_id"], "planner", "failed", "LLM call failed after retries")
                raise

            subtasks = self._parse_and_validate(response)
            self._check_capabilities(state["agent_id"], subtasks)

            state["subtasks"] = subtasks
            state["current_subtask_index"] = 0
            state["status"] = TaskStatus.SIMULATING

            await _log_step(self.deps, state["task_id"], state["agent_id"], "planner", "succeeded", f"{len(subtasks)} subtasks")
            return state

    def _parse_and_validate(self, raw_response: str) -> list[SubTask]:
        try:
            validated = PlannerOutputModel.model_validate_json(raw_response)
        except ValidationError as exc:
            logger.error("Planner: LLM output failed validation: %s", exc)
            raise PlannerParseError(str(exc)) from exc

        return [st.model_dump() for st in validated.subtasks]

    def _check_capabilities(self, agent_id: str, subtasks: list[SubTask]) -> None:
        """Checks each subtask's required capability against the agent's
        on-chain bitmask BEFORE simulation — catching a capability denial
        here is cheaper than discovering it after gas estimation."""
        cache_key = f"capabilities:{agent_id}"
        capabilities = self.deps.capability_cache.get(cache_key)

        if capabilities is None:
            agent_record = self.deps.agent_registry_client.get_agent(int(agent_id))
            capabilities = agent_record["capabilities"]
            self.deps.capability_cache.set(cache_key, capabilities)

        for subtask in subtasks:
            required = TOOL_REQUIRED_CAPABILITY.get(subtask["tool"])
            if required is None:
                continue
            capability_bit = getattr(self.deps.agent_registry_client.contract.functions, required, None)
            # Bit values live on CapabilityRegistry.sol, not AgentRegistry — resolved
            # via the registry client's cached constants rather than re-deriving them here.
            has_capability = self.deps.agent_registry_client.has_capability(int(agent_id), required)
            if not has_capability:
                raise CapabilityDeniedError(agent_id, required)

class MemoryKnowledgeAgent:
    def __init__(self, deps: Dependencies):
        self.deps = deps

    async def run(self, state: AgentState) -> AgentState:
        with traced_span("memory.run", task_id=state["task_id"]):
            agent_id = state["agent_id"]
            logger.info("Memory: gathering context for task %s", state["task_id"])

            try:
                portfolio = await with_retry(self.deps.portfolio_client.get_portfolio, agent_id, max_retries=2)
                history = await self._get_recent_history(agent_id, limit=10)
                rag_results = await self._retrieve_relevant_docs(state["user_request"])
            except TransientDependencyError as exc:
                await _log_step(self.deps, state["task_id"], agent_id, "memory", "failed", str(exc))
                raise MemoryRetrievalError(str(exc)) from exc

            state["context"] = {
                "portfolio": portfolio,
                "recent_history": history,
                "relevant_docs": rag_results,
            }

            await _log_step(self.deps, state["task_id"], agent_id, "memory", "succeeded")
            return state

    async def _get_recent_history(self, agent_id: str, limit: int) -> list[dict]:
        from sqlalchemy import select
        from db.models.execution_history import ExecutionStep

        async with self.deps.db_session_factory() as session:
            stmt = (
                select(ExecutionStep)
                .where(ExecutionStep.agent_id == agent_id)
                .order_by(ExecutionStep.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {"role": r.role, "status": r.status, "created_at": r.created_at.isoformat()}
                for r in rows
            ]

    async def _retrieve_relevant_docs(self, query: str, top_k: int = 5) -> list[dict]:
        embedding = await with_retry(self.deps.vector_store.embed, query, max_retries=2)
        results = await with_retry(self.deps.vector_store.query, embedding, top_k=top_k, max_retries=2)
        return [{"content": r.content, "score": r.score} for r in results]


# ============================================================================
# SIMULATOR
# ============================================================================

class SimulatorAgent:
    def __init__(self, deps: Dependencies):
        self.deps = deps

    async def run(self, state: AgentState) -> AgentState:
        with traced_span("simulator.run", task_id=state["task_id"], agent_id=state["agent_id"]):
            subtask = state["subtasks"][state["current_subtask_index"]]
            logger.info("Simulator: checking subtask %s for task %s", subtask["tool"], state["task_id"])

            try:
                target, value, calldata = await self._resolve_call(state["agent_id"], subtask)
            except Exception as exc:
                await _log_step(self.deps, state["task_id"], state["agent_id"], "simulator", "failed", str(exc))
                raise ToolResolutionError(str(exc)) from exc

            estimated_gas = await self._estimate_gas(target, value, calldata)
            estimated_slippage_bps = await self._estimate_slippage(subtask)
            risk_score = self._score_risk(subtask, estimated_slippage_bps)

            passed, reason = await with_retry(
                self.deps.policy_engine_client.check_action,
                agent_id=state["agent_id"], target=target, value=value, data=calldata,
                max_retries=3, circuit=self.deps.policy_circuit,
            )

            result: SimulationResult = {
                "estimated_gas": estimated_gas,
                "estimated_slippage_bps": estimated_slippage_bps,
                "risk_score": risk_score,
                "passed_policy_check": passed,
                "policy_reason": reason,
            }

            state["simulation"] = result
            state["status"] = TaskStatus.EXECUTING if passed else TaskStatus.FAILED

            if not passed:
                await _log_step(self.deps, state["task_id"], state["agent_id"], "simulator", "failed", f"Policy rejected: {reason}")
                raise PolicyRejectedError(reason)

            await _log_step(self.deps, state["task_id"], state["agent_id"], "simulator", "succeeded", f"risk_score={risk_score}")
            return state

    async def _resolve_call(self, agent_id: str, subtask: dict) -> tuple[str, int, bytes]:
        """Real integration: dispatches to tool-router's actual tool
        classes (swap_tool.py, defi_tool.py, etc.) via their build_call
        interface, rather than raising NotImplementedError."""
        tool = self.deps.tool_router.get_tool(subtask["tool"])
        if tool is None:
            raise ToolResolutionError(f"Tool not registered: {subtask['tool']}")

        tool_call = await tool.build_call(subtask["action"], {**subtask["params"], "agent_id": agent_id})
        return tool_call.target, tool_call.value, tool_call.calldata

    async def _estimate_gas(self, target: str, value: int, calldata: bytes) -> int:
        return await with_retry(
            self.deps.chain_client.estimate_gas, to=target, value=value, data=calldata,
            max_retries=3, circuit=self.deps.chain_circuit,
        )

    async def _estimate_slippage(self, subtask: dict) -> int:
        if subtask["tool"] != "swap_tool":
            return 0
        # TODO: query the DEX quote (e.g. Uniswap v4 quoter) and compare
        # expected output to the pool's current price. Left as the one
        # genuine placeholder in this file — the DEX quoter integration
        # needs the specific pool addresses from your deployment config,
        # which isn't something to guess at.
        return 50

    def _score_risk(self, subtask: dict, slippage_bps: int) -> int:
        score = 0
        if subtask["tool"] == "bridge_tool":
            score += 40
        if subtask["tool"] == "defi_tool" and subtask["action"] == "leverage":
            score += 50
        score += min(slippage_bps // 10, 30)
        return min(score, 100)