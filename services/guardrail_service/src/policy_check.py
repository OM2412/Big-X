import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


@dataclass
class PolicyCheckResult:
    allowed: bool
    reason: str | None = None


# Restricted jurisdictions and feature flags would normally come from a
# config service / feature-flag provider rather than being hard-coded here.
RESTRICTED_ACTION_TYPES_BY_TIER = {
    "free": {"bridge", "lending"},       # higher-risk actions gated to paid tiers
    "pro": set(),
    "enterprise": set(),
}
MAX_DAILY_ACTIONS_BY_TIER = {
    "free": 10,
    "pro": 100,
    "enterprise": 10_000,
}


class PolicyCheckService:
    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory

    async def check(self, user_id: str, action_type: str) -> PolicyCheckResult:
        user = await self._get_user(user_id)
        if user is None:
            return PolicyCheckResult(allowed=False, reason="User not found")

        if not user["is_active"]:
            return PolicyCheckResult(allowed=False, reason="Account is suspended")

        tier = user.get("tier", "free")

        if action_type in RESTRICTED_ACTION_TYPES_BY_TIER.get(tier, set()):
            return PolicyCheckResult(
                allowed=False,
                reason=f"'{action_type}' requires a higher account tier",
            )

        daily_count = await self._get_daily_action_count(user_id)
        limit = MAX_DAILY_ACTIONS_BY_TIER.get(tier, 10)
        if daily_count >= limit:
            return PolicyCheckResult(allowed=False, reason="Daily action limit reached")

        return PolicyCheckResult(allowed=True)

    async def _get_user(self, user_id: str) -> dict | None:
        async with self.db_session_factory() as session:
            # TODO: query db.models.users.User by id, return the fields
            # this method needs (is_active, tier).
            return {"is_active": True, "tier": "free"}

    async def _get_daily_action_count(self, user_id: str) -> int:
        async with self.db_session_factory() as session:
            # TODO: count db.models.execution_history.ExecutionStep rows
            # for this user's agents where role == "planner" and
            # created_at >= datetime.utcnow() - timedelta(days=1).
            return 0