# agents/simulator.py
#
# Runs before Executor. Estimates gas/slippage for the current subtask and
# calls the on-chain PolicyEngine's checkAction (read-only, no gas cost) to
# confirm the action is actually allowed before anything gets submitted.
# This is the checkpoint that stops a bad plan from ever reaching a wallet.

import logging
import os

from .state import AgentState, SimulationResult

logger = logging.getLogger(__name__)


class SimulatorAgent:
    def __init__(self, chain_client, policy_engine_client, price_oracle):
        self.chain_client = chain_client                # RPC wrapper, eth_estimateGas etc.
        self.policy_engine_client = policy_engine_client  # calls PolicyEngine.checkAction on-chain
        self.price_oracle = price_oracle                  # for USD-denominated risk scoring

    async def run(self, state: AgentState) -> AgentState:
        subtask = state["subtasks"][state["current_subtask_index"]]
        logger.info("Simulator: checking subtask %s for task %s", subtask["tool"], state["task_id"])

        estimated_gas = await self._estimate_gas(subtask)
        estimated_slippage_bps = await self._estimate_slippage(subtask)
        risk_score = self._score_risk(subtask, estimated_slippage_bps)

        target_address, value, calldata = self._resolve_call(subtask)
        passed, reason = await self.policy_engine_client.check_action(
            agent_id=state["agent_id"],
            target=target_address,
            value=value,
            data=calldata,
        )

        result: SimulationResult = {
            "estimated_gas": estimated_gas,
            "estimated_slippage_bps": estimated_slippage_bps,
            "risk_score": risk_score,
            "passed_policy_check": passed,
            "policy_reason": reason,
        }

        state["simulation"] = result
        state["status"] = "executing" if passed else "failed"
        return state

    async def _estimate_gas(self, subtask: dict) -> int:
        target, value, calldata = self._resolve_call(subtask)
        return await self.chain_client.estimate_gas(to=target, value=value, data=calldata)

    async def _estimate_slippage(self, subtask: dict) -> int:
        if subtask["tool"] != "swap_tool":
            return 0
        # TODO: query the DEX quote (e.g. Uniswap v4 quoter) and compare
        # expected output to the pool's current price to get real slippage bps.
        return 50  # placeholder: 0.5%

    def _score_risk(self, subtask: dict, slippage_bps: int) -> int:
        score = 0
        if subtask["tool"] == "bridge_tool":
            score += 40  # bridging is inherently higher risk
        if subtask["tool"] == "defi_tool" and subtask["action"] == "leverage":
            score += 50
        score += min(slippage_bps // 10, 30)
        return min(score, 100)

    def _resolve_call(self, subtask: dict) -> tuple[str, int, bytes]:
        # TODO: translate a subtask (tool/action/params) into an actual
        # (target address, value, calldata) tuple via the Tool Router's
        # encoding logic. Kept as a stub here since it's tool-specific.
        if os.getenv("AGENT_ORCHESTRATOR_MODE", "local").lower() == "local":
            return "0x" + "0" * 40, 0, b""
        raise NotImplementedError("Wire this up to services/tool_router")