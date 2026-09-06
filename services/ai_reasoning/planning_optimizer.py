import logging

from .long_term import LongTermMemoryStore

logger = logging.getLogger(__name__)

READ_ONLY_TOOLS = {"price_feed_tool", "oracle_tool"}


class PlanOptimizer:
    def __init__(self, memory_store: LongTermMemoryStore):
        self.memory_store = memory_store

    async def optimize(self, agent_id: str, subtasks: list[dict]) -> list[dict]:
        subtasks = self._merge_duplicate_actions(subtasks)
        subtasks = self._reorder_reads_before_writes(subtasks)
        subtasks = await self._drop_known_failures(agent_id, subtasks)
        return subtasks

    def _merge_duplicate_actions(self, subtasks: list[dict]) -> list[dict]:
        """Two consecutive subtasks with the same tool+action+asset get
        merged into one with summed amounts — e.g. Planner producing
        "swap $50 to BTC" then "swap $50 to BTC" (a common LLM artifact
        when a request implies two similar steps) becomes one $100 swap,
        saving one transaction's worth of gas."""
        if not subtasks:
            return subtasks

        merged = [subtasks[0]]
        for subtask in subtasks[1:]:
            prev = merged[-1]
            if self._is_mergeable(prev, subtask):
                prev["params"]["amount_usd"] = prev["params"].get("amount_usd", 0) + subtask["params"].get("amount_usd", 0)
                logger.info("Merged duplicate %s/%s subtasks", subtask["tool"], subtask["action"])
            else:
                merged.append(subtask)
        return merged

    def _is_mergeable(self, a: dict, b: dict) -> bool:
        if a["tool"] != b["tool"] or a["action"] != b["action"]:
            return False
        # Same asset pair / protocol — compare whatever identifying params exist
        identifying_keys = {"asset", "token_in", "token_out", "protocol"}
        a_identity = {k: v for k, v in a["params"].items() if k in identifying_keys}
        b_identity = {k: v for k, v in b["params"].items() if k in identifying_keys}
        return a_identity == b_identity

    def _reorder_reads_before_writes(self, subtasks: list[dict]) -> list[dict]:
        """Stable-sorts read-only subtasks (price checks) ahead of writes
        within the plan, so the Simulator's risk scoring for the writes
        that follow has the freshest possible price data, without
        changing the relative order of the writes themselves."""
        reads = [st for st in subtasks if st["tool"] in READ_ONLY_TOOLS]
        writes = [st for st in subtasks if st["tool"] not in READ_ONLY_TOOLS]
        return reads + writes

    async def _drop_known_failures(self, agent_id: str, subtasks: list[dict]) -> list[dict]:
        """If this exact tool has failed for this agent every single time
        in recent history, drop it from the plan rather than let it reach
        the Simulator, and log why — the Planner's LLM has no memory of
        past failures on its own, this is what gives it one."""
        filtered = []
        for subtask in subtasks:
            failures = await self.memory_store.get_failure_patterns(agent_id, subtask["tool"], limit=5)
            if len(failures) >= 5:
                logger.warning(
                    "Dropping subtask %s/%s: last %d attempts for this agent all failed",
                    subtask["tool"], subtask["action"], len(failures),
                )
                continue
            filtered.append(subtask)

        if not filtered:
            logger.error("PlanOptimizer dropped every subtask due to failure history — returning original plan unfiltered")
            return subtasks  # never return an empty plan silently — better to let Simulator/PolicyEngine reject it explicitly

        return filtered