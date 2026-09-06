

import time
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SpendLimitConfig:
    per_tx_limit_usd: float
    daily_limit_usd: float


@dataclass
class SpendCheckResult:
    allowed: bool
    reason: str | None = None
    remaining_daily_usd: float | None = None


class SpendLimitsService:
    def __init__(self, redis_client, config_store):
        self.redis = redis_client
        self.config_store = config_store  # resolves agent_id -> SpendLimitConfig (DB-backed)

    async def check(self, agent_id: str, amount_usd: float) -> SpendCheckResult:
        config = await self.config_store.get_config(agent_id)
        if config is None:
            return SpendCheckResult(allowed=False, reason="No spend limit configured for this agent")

        if amount_usd > config.per_tx_limit_usd:
            return SpendCheckResult(
                allowed=False,
                reason=f"${amount_usd:,.2f} exceeds per-transaction limit of ${config.per_tx_limit_usd:,.2f}",
            )

        spent_today = await self._get_spent_today(agent_id)
        remaining = config.daily_limit_usd - spent_today

        if amount_usd > remaining:
            return SpendCheckResult(
                allowed=False,
                reason=f"${amount_usd:,.2f} would exceed daily limit — ${remaining:,.2f} remaining",
                remaining_daily_usd=remaining,
            )

        return SpendCheckResult(allowed=True, remaining_daily_usd=remaining - amount_usd)

    async def record_spend(self, agent_id: str, amount_usd: float) -> None:
        """Call AFTER a transaction actually confirms — matches the same
        pattern as PolicyEngine.sol's recordSpend, kept in sync deliberately
        so both layers agree on what's actually been spent."""
        key = self._daily_key(agent_id)
        pipe = self.redis.pipeline()
        pipe.incrbyfloat(key, amount_usd)
        pipe.expire(key, 86400)  # auto-expires at end of window, no manual reset job needed
        await pipe.execute()

    async def _get_spent_today(self, agent_id: str) -> float:
        raw = await self.redis.get(self._daily_key(agent_id))
        return float(raw) if raw else 0.0

    def _daily_key(self, agent_id: str) -> str:
        # Bucketed by UTC day — resets naturally at midnight UTC rather than
        # tracking a rolling 24h window, matching PolicyEngine.sol's
        # `block.timestamp / 1 days` bucketing so both layers agree on
        # when "today" resets.
        day_bucket = int(time.time()) // 86400
        return f"spend_limit:{agent_id}:{day_bucket}"