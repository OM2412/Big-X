
import logging

from .long_term import LongTermMemoryStore
from .tool_selector import ToolSelector

logger = logging.getLogger(__name__)

# How far off a prediction has to be before it's worth writing a lesson
# about — small variance is normal and not worth cluttering memory with.
GAS_ERROR_THRESHOLD_PCT = 20.0
SLIPPAGE_ERROR_THRESHOLD_BPS = 30


class ReflectionAgent:
    def __init__(self, memory_store: LongTermMemoryStore, tool_selector: ToolSelector, llm_client=None):
        self.memory_store = memory_store
        self.tool_selector = tool_selector
        self.llm_client = llm_client  # optional — used for a richer natural-language summary; falls back to a templated one

    async def reflect(self, state: dict) -> None:
        """Call this once per completed subtask, after Critic has run.
        Compares simulation predictions to actual execution results,
        writes a memory if the gap is significant, and updates
        ToolSelector's running performance stats regardless."""
        simulation = state.get("simulation")
        execution = state.get("execution")
        if not simulation or not execution:
            return

        subtask = state["subtasks"][state["current_subtask_index"]]
        agent_id = state["agent_id"]
        outcome = "success" if execution["status"] == "confirmed" else "failure"

        gas_error_pct = self._compute_gas_error(simulation, execution)
        slippage_error_bps = self._compute_slippage_error(subtask, execution)

        await self.tool_selector.record_outcome(
            tool=subtask["tool"], protocol=subtask["params"].get("protocol"),
            success=(outcome == "success"), gas_error_pct=gas_error_pct or 0.0,
            slippage_error_bps=slippage_error_bps or 0,
        )

        should_write_memory = (
            outcome == "failure"
            or (gas_error_pct is not None and abs(gas_error_pct) > GAS_ERROR_THRESHOLD_PCT)
            or (slippage_error_bps is not None and abs(slippage_error_bps) > SLIPPAGE_ERROR_THRESHOLD_BPS)
        )
        if not should_write_memory:
            return

        summary = await self._summarize(subtask, simulation, execution, outcome, gas_error_pct, slippage_error_bps)

        await self.memory_store.write_memory(
            agent_id=agent_id, task_id=state["task_id"], summary=summary, outcome=outcome,
            tool=subtask["tool"], protocol=subtask["params"].get("protocol"),
            predicted_gas=simulation["estimated_gas"], actual_gas=execution.get("actual_gas_used"),
            predicted_slippage_bps=simulation["estimated_slippage_bps"], actual_slippage_bps=None,
        )

    def _compute_gas_error(self, simulation: dict, execution: dict) -> float | None:
        predicted = simulation.get("estimated_gas")
        actual = execution.get("actual_gas_used")
        if not predicted or not actual:
            return None
        return ((actual - predicted) / predicted) * 100

    def _compute_slippage_error(self, subtask: dict, execution: dict) -> int | None:
        # Actual realized slippage isn't in ExecutionResult today — would
        # need the swap's actual output amount vs. quoted amount, which
        # means swap_tool.py's build_call needs to return enough info to
        # compute this post-hoc, or the Executor needs to read it from the
        # transaction receipt's logs. Left unresolved rather than faking
        # a number — this is a real gap, not a placeholder to hide.
        return None

    async def _summarize(self, subtask: dict, simulation: dict, execution: dict, outcome: str, gas_error_pct: float | None, slippage_error_bps: int | None) -> str:
        if self.llm_client is not None:
            prompt = (
                f"Summarize this execution outcome in one sentence, focused on what to remember "
                f"for future planning: tool={subtask['tool']}, action={subtask['action']}, "
                f"outcome={outcome}, gas_error_pct={gas_error_pct}, error={execution.get('error')}"
            )
            return await self.llm_client.complete(system="Be concise and specific.", user=prompt, context={})

        # Templated fallback if no LLM client is wired — still useful, just less natural.
        if outcome == "failure":
            return f"{subtask['tool']}/{subtask['action']} failed: {execution.get('error', 'unknown error')}"
        return f"{subtask['tool']}/{subtask['action']} succeeded but gas estimate was off by {gas_error_pct:.1f}%"