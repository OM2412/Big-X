# agents/executor.py
#
# Runs after Simulator passes. Actually submits the transaction through the
# agent's Token Bound Account (ERC-6551), waits for confirmation, records it
# in the transactions table, and reports the on-chain PolicyEngine spend.

import logging

from .state import AgentState, ExecutionResult

logger = logging.getLogger(__name__)


class ExecutorAgent:
    def __init__(self, tool_router, chain_client, policy_engine_client, db_session_factory):
        self.tool_router = tool_router                    # dispatches to swap/bridge/defi/nft tools
        self.chain_client = chain_client                    # RPC wrapper for submit + wait-for-receipt
        self.policy_engine_client = policy_engine_client      # records spend after successful execution
        self.db_session_factory = db_session_factory

    async def run(self, state: AgentState) -> AgentState:
        subtask = state["subtasks"][state["current_subtask_index"]]
        simulation = state["simulation"]

        if not simulation or not simulation["passed_policy_check"]:
            state["execution"] = {
                "tx_hash": None,
                "status": "failed",
                "actual_gas_used": None,
                "error": "Blocked by policy check before execution",
            }
            state["status"] = "reviewing"
            return state

        logger.info("Executor: submitting subtask %s for task %s", subtask["tool"], state["task_id"])

        try:
            tx_hash = await self.tool_router.dispatch(
                agent_id=state["agent_id"],
                tool=subtask["tool"],
                action=subtask["action"],
                params=subtask["params"],
            )
            receipt = await self.chain_client.wait_for_receipt(tx_hash)

            result: ExecutionResult = {
                "tx_hash": tx_hash,
                "status": "confirmed" if receipt.success else "failed",
                "actual_gas_used": receipt.gas_used,
                "error": None if receipt.success else "Transaction reverted",
            }

            if receipt.success:
                await self.policy_engine_client.record_spend(
                    agent_id=state["agent_id"],
                    value=subtask["params"].get("amount_usd", 0),
                )

            await self._log_transaction(state, subtask, result)

        except Exception as exc:  # noqa: BLE001 — deliberately broad, Critic decides how to respond
            logger.exception("Executor: subtask failed for task %s", state["task_id"])
            result = {"tx_hash": None, "status": "failed", "actual_gas_used": None, "error": str(exc)}

        state["execution"] = result
        state["status"] = "reviewing"
        return state

    async def _log_transaction(self, state: AgentState, subtask: dict, result: ExecutionResult) -> None:
        async with self.db_session_factory() as session:
            # TODO: insert into db.models.transactions.Transaction using
            # state["agent_id"], subtask, and result — status/tx_hash/gas_used
            # map directly onto that model's fields.
            pass