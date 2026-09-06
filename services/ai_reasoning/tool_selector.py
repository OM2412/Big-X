
import logging
from dataclasses import dataclass

from sqlalchemy import select

logger = logging.getLogger(__name__)

# Weights for the composite score — success rate matters most (a fast,
# cheap protocol that fails often is worse than a reliable slower one),
# then estimate accuracy (predictability matters for policy/risk
# decisions), then raw gas cost.
WEIGHT_SUCCESS_RATE = 0.5
WEIGHT_ESTIMATE_ACCURACY = 0.3
WEIGHT_LATENCY = 0.2

MIN_ATTEMPTS_FOR_CONFIDENCE = 5  # below this, treat stats as unreliable and fall back to a neutral score


@dataclass
class ProtocolScore:
    protocol: str
    score: float
    success_rate: float
    sample_size: int


class ToolSelector:
    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory

    async def select_best(self, tool: str, candidate_protocols: list[str], chain_id: int) -> str:
        """Called by PlannerAgent (or PlanOptimizer) when a subtask's
        params don't already pin a specific protocol — returns the
        best-scoring candidate. If none have enough history, returns the
        first candidate unchanged rather than making a low-confidence
        choice look authoritative."""
        scores = await self._score_candidates(tool, candidate_protocols, chain_id)

        if not scores:
            logger.info("No performance history for %s candidates %s, using first as default", tool, candidate_protocols)
            return candidate_protocols[0]

        best = max(scores, key=lambda s: s.score)
        logger.info("Selected %s for %s (score=%.2f, success_rate=%.0f%%, n=%d)", best.protocol, tool, best.score, best.success_rate * 100, best.sample_size)
        return best.protocol

    async def _score_candidates(self, tool: str, protocols: list[str], chain_id: int) -> list[ProtocolScore]:
        from db.models.ai_reasoning import ToolPerformanceStat

        scores = []
        async with self.db_session_factory() as session:
            for protocol in protocols:
                stmt = select(ToolPerformanceStat).where(
                    ToolPerformanceStat.tool == tool,
                    ToolPerformanceStat.protocol == protocol,
                    ToolPerformanceStat.chain_id == chain_id,
                )
                result = await session.execute(stmt)
                stat = result.scalar_one_or_none()

                if stat is None or stat.total_attempts < MIN_ATTEMPTS_FOR_CONFIDENCE:
                    continue  # not enough data to score confidently — excluded, not scored as 0

                avg_gas_error = abs(stat.total_gas_estimate_error_pct / stat.total_attempts)
                estimate_accuracy = max(0.0, 1.0 - (avg_gas_error / 100))
                latency_score = max(0.0, 1.0 - (stat.avg_execution_latency_ms / 30000))  # normalize against a 30s worst-case

                composite = (
                    stat.success_rate * WEIGHT_SUCCESS_RATE
                    + estimate_accuracy * WEIGHT_ESTIMATE_ACCURACY
                    + latency_score * WEIGHT_LATENCY
                )
                scores.append(ProtocolScore(protocol=protocol, score=composite, success_rate=stat.success_rate, sample_size=stat.total_attempts))

        return scores

    async def record_outcome(self, tool: str, protocol: str | None, success: bool, gas_error_pct: float, slippage_error_bps: int) -> None:
        """Called by ReflectionAgent after every subtask execution —
        updates the running stats this class scores against next time."""
        if protocol is None:
            return  # nothing to attribute the outcome to

        from db.models.ai_reasoning import ToolPerformanceStat

        async with self.db_session_factory() as session:
            stmt = select(ToolPerformanceStat).where(ToolPerformanceStat.tool == tool, ToolPerformanceStat.protocol == protocol)
            result = await session.execute(stmt)
            stat = result.scalar_one_or_none()

            if stat is None:
                stat = ToolPerformanceStat(tool=tool, protocol=protocol, chain_id=0, total_attempts=0, successful_attempts=0)
                session.add(stat)

            stat.total_attempts += 1
            if success:
                stat.successful_attempts += 1
            stat.total_gas_estimate_error_pct += gas_error_pct
            stat.total_slippage_estimate_error_bps += slippage_error_bps

            await session.commit()