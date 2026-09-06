
import json
import logging

from .state import AgentState, SubTask

logger = logging.getLogger(__name__)

# Tool catalog the planner is allowed to route into — keep this in sync with
# services/tool_router/tools.py. Passed into the LLM prompt so it can't
# invent a tool that doesn't exist.
AVAILABLE_TOOLS = [
    "swap_tool",       # DEX swaps (Uniswap v4, etc.)
    "bridge_tool",      # cross-chain asset moves
    "defi_tool",        # Aave, Compound deposits/withdrawals
    "nft_tool",         # NFT buy/sell
    "wallet_tool",       # balance checks, transfers
    "price_feed_tool",   # read-only market data
]

PLANNER_SYSTEM_PROMPT = """You are a planning agent for an on-chain execution system.
Break the user's request into an ordered list of subtasks. Each subtask must
use one of these tools: {tools}.

Respond ONLY as a JSON array of objects with keys: tool, action, params.
Example: [{{"tool": "swap_tool", "action": "buy", "params": {{"asset": "BTC", "amount_usd": 100}}}}]
"""


class PlannerAgent:
    def __init__(self, llm_client):
        # llm_client is your Claude/GPT API wrapper — kept generic here so
        # this file doesn't hard-code a provider.
        self.llm_client = llm_client

    async def run(self, state: AgentState) -> AgentState:
        logger.info("Planner: breaking down request for task %s", state["task_id"])

        prompt = PLANNER_SYSTEM_PROMPT.format(tools=", ".join(AVAILABLE_TOOLS))
        response = await self.llm_client.complete(
            system=prompt,
            user=state["user_request"],
            context=state.get("context", {}),
        )

        subtasks = self._parse_subtasks(response)

        state["subtasks"] = subtasks
        state["current_subtask_index"] = 0
        state["status"] = "simulating"
        return state

    def _parse_subtasks(self, raw_response: str) -> list[SubTask]:
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError:
            logger.error("Planner: LLM returned non-JSON response: %s", raw_response)
            raise ValueError("Planner failed to produce a valid subtask list")

        for subtask in parsed:
            if subtask.get("tool") not in AVAILABLE_TOOLS:
                raise ValueError(f"Planner referenced unknown tool: {subtask.get('tool')}")

        return parsed