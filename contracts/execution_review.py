# agents/execution_review.py
#
# Executor + Critic — the fourth and fifth nodes of the graph. Split from
# the LangGraph wiring (graph/langgraph_flow.py), which imports both
# classes from here.

import logging

from state import AgentState, ExecutionResult, Critique
from exceptions import TransientDependencyError
from resilience import traced_span, with_retry, CircuitBreaker
from planning_pipeline import Dependencies, _log_step
from state_enums import TaskStatus

logger = logging.getLogger(__name__)

# Errors here mean "retrying without a plan change won't help" — Critic
# treats these as transient (worth a full replan) vs. errors that mean
# "this will never succeed" (policy rejection, capability denial), which
# should hard-stop instead of burning a retry.
TRANSIENT_EXECUTION_ERRORS = {"Transaction reverted", "timeout", "nonce too low", "replacement transaction underpriced"}


# ============================================================================
# EXECUTOR — submits through tool-router (which internally resolves to
# WalletService.submit()), waits for confirmation, records spend back to
# PolicyEngine, and writes the outcome to execution_history.
# ============================================================================

class ExecutorAgent:
    def __init__(self, deps: Dependencies):
        self.deps = deps
        self.execution_circuit = CircuitBreaker()

    async def run(self, state: AgentState) -> AgentState:
        with traced_span("executor.run", task_id=state["task_id"], agent_id=state["agent_id"]):
            subtask = state["subtasks"][state["current_subtask_index"]]
            simulation = state["simulation"]

            if not simulation or not simulation["passed_policy_check"]:
                # Should never actually reach here — SimulatorAgent raises
                # PolicyRejectedError before the graph advances to Executor.
                # Guarded anyway since state is a plain dict a future change
                # could bypass that invariant.
                result: ExecutionResult = {
                    "tx_hash": None, "status": "failed", "actual_gas_used": None,
                    "error": "Blocked by policy check before execution",
                }
                state["execution"] = result
                state["status"] = TaskStatus.REVIEWING
                return state

            logger.info("Executor: submitting subtask %s for task %s", subtask["tool"], state["task_id"])

            try:
                tx_hash = await with_retry(
                    self.deps.tool_router.dispatch,
                    agent_id=state["agent_id"], tool=subtask["tool"],
                    action=subtask["action"], params=subtask["params"],
                    max_retries=2, circuit=self.execution_circuit,
                )
                receipt = await with_retry(
                    self.deps.chain_client.wait_for_receipt, tx_hash,
                    max_retries=3, circuit=self.deps.chain_circuit,
                )

                result = {
                    "tx_hash": tx_hash,
                    "status": "confirmed" if receipt.success else "failed",
                    "actual_gas_used": receipt.gas_used,
                    "error": None if receipt.success else "Transaction reverted",
                }

                if receipt.success:
                    await with_retry(
                        self.deps.policy_engine_client.record_spend,
                        agent_id=state["agent_id"], value=subtask["params"].get("amount_usd", 0),
                        max_retries=2,
                    )

            except TransientDependencyError as exc:
                result = {"tx_hash": None, "status": "failed", "actual_gas_used": None, "error": str(exc)}
            except Exception as exc:  # noqa: BLE001 — Critic decides retry vs. hard-stop, so surface everything
                logger.exception("Executor: subtask failed for task %s", state["task_id"])
                result = {"tx_hash": None, "status": "failed", "actual_gas_used": None, "error": str(exc)}

            state["execution"] = result
            state["status"] = "reviewing"

            await _log_step(
                self.deps, state["task_id"], state["agent_id"], "executor",
                "succeeded" if result["status"] == "confirmed" else "failed",
                result.get("tx_hash") or result.get("error"),
            )
            return state


# ============================================================================
# CRITIC — decides confirmed -> next subtask, transient failure -> retry
# (routes back to Planner), or hard failure -> stop.
# ============================================================================

class CriticAgent:
    def __init__(self, deps: Dependencies):
        self.deps = deps

    async def run(self, state: AgentState) -> AgentState:
        with traced_span("critic.run", task_id=state["task_id"], agent_id=state["agent_id"]):
            execution = state["execution"]
            logger.info("Critic: reviewing outcome for task %s", state["task_id"])

            critique = self._evaluate(execution)
            state["critique"] = critique

            more_subtasks_remain = state["current_subtask_index"] + 1 < len(state["subtasks"])

            if execution["status"] == "confirmed" and more_subtasks_remain:
                state["current_subtask_index"] += 1
                state["status"] = TaskStatus.SIMULATING
            elif execution["status"] == "confirmed":
                state["status"] = TaskStatus.DONE
            elif critique["should_retry"] and state["retry_count"] < state["max_retries"]:
                state["retry_count"] += 1
                state["status"] = TaskStatus.RETRYING
            else:
                state["status"] = TaskStatus.FAILED

            await _log_step(self.deps, state["task_id"], state["agent_id"], "critic", state["status"], critique["feedback"])
            return state

    def _evaluate(self, execution: dict) -> Critique:
        if execution["status"] == "confirmed":
            return {"outcome_matches_intent": True, "should_retry": False, "feedback": "Execution confirmed on-chain as expected."}

        error = execution.get("error") or "Unknown execution failure"
        is_transient = any(pattern in error for pattern in TRANSIENT_EXECUTION_ERRORS)

        return {"outcome_matches_intent": False, "should_retry": is_transient, "feedback": error}