# agents/critic.py
#
# Last node in the graph. Checks whether the execution result actually
# matches what the user asked for, decides whether to retry (feeding back
# to Planner) or mark the task done/failed, and logs the outcome to
# execution_history for the audit trail.

import logging

from .state import AgentState, Critique

logger = logging.getLogger(__name__)


class CriticAgent:
    def __init__(self, llm_client, db_session_factory):
        self.llm_client = llm_client
        self.db_session_factory = db_session_factory

    async def run(self, state: AgentState) -> AgentState:
        execution = state["execution"]
        logger.info("Critic: reviewing outcome for task %s", state["task_id"])

        critique = self._evaluate(state, execution)
        state["critique"] = critique

        await self._log_step(state, critique)

        more_subtasks_remain = state["current_subtask_index"] + 1 < len(state["subtasks"])

        if execution["status"] == "confirmed" and more_subtasks_remain:
            state["current_subtask_index"] += 1
            state["status"] = "simulating"  # loop back for the next subtask
        elif execution["status"] == "confirmed":
            state["status"] = "done"
        elif critique["should_retry"] and state["retry_count"] < state["max_retries"]:
            state["retry_count"] += 1
            state["status"] = "retrying"  # loop back to Planner via the feedback/retry edge
        else:
            state["status"] = "failed"

        return state

    def _evaluate(self, state: AgentState, execution: dict) -> Critique:
        if execution["status"] == "confirmed":
            return {
                "outcome_matches_intent": True,
                "should_retry": False,
                "feedback": "Execution confirmed on-chain as expected.",
            }

        # Failed or blocked — decide if this is worth retrying (e.g. transient
        # gas spike) versus a hard stop (e.g. policy rejection, which won't
        # change on retry without a human raising the limit).
        transient_errors = {"Transaction reverted", "timeout", "nonce too low"}
        is_transient = any(err in (execution.get("error") or "") for err in transient_errors)

        return {
            "outcome_matches_intent": False,
            "should_retry": is_transient,
            "feedback": execution.get("error") or "Unknown execution failure",
        }

    async def _log_step(self, state: AgentState, critique: Critique) -> None:
        async with self.db_session_factory() as session:
            # TODO: insert into db.models.execution_history.ExecutionStep with
            # role="critic", status derived from critique, task_id=state["task_id"].
            pass