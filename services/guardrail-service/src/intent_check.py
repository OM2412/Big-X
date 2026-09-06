# intent_check.py
#
# Layer 2, first check. Classifies what the user is actually asking for
# before anything reaches the orchestrator — is this a legitimate DeFi
# action request, a question, or something off-scope/suspicious.

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class IntentCategory(str, Enum):
    DEFI_ACTION = "defi_action"       # "buy $100 of BTC", "bridge to Base"
    PORTFOLIO_QUERY = "portfolio_query"  # "what's my balance"
    GENERAL_QUESTION = "general_question"
    OFF_SCOPE = "off_scope"            # unrelated to the platform's purpose
    SUSPICIOUS = "suspicious"           # looks like an attempt to manipulate the agent


@dataclass
class IntentResult:
    category: IntentCategory
    confidence: float
    allowed: bool
    reason: str | None = None


# Fast heuristic pass before an LLM call — catches the obvious cases cheaply.
_ACTION_KEYWORDS = {"buy", "sell", "swap", "bridge", "deposit", "withdraw", "stake", "farm", "trade"}
_QUERY_KEYWORDS = {"balance", "portfolio", "how much", "what's my", "show me my"}
_SUSPICIOUS_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard your instructions",
    "you are now",
    "system prompt",
    "reveal your instructions",
)


class IntentCheckAgent:
    def __init__(self, llm_client=None):
        # llm_client is optional — heuristics handle the common cases;
        # fall back to an LLM classification only for ambiguous input.
        self.llm_client = llm_client

    async def check(self, user_message: str) -> IntentResult:
        lowered = user_message.lower()

        if any(pattern in lowered for pattern in _SUSPICIOUS_PATTERNS):
            return IntentResult(
                category=IntentCategory.SUSPICIOUS,
                confidence=0.9,
                allowed=False,
                reason="Message contains a known prompt-injection pattern",
            )

        if any(kw in lowered for kw in _ACTION_KEYWORDS):
            return IntentResult(category=IntentCategory.DEFI_ACTION, confidence=0.8, allowed=True)

        if any(kw in lowered for kw in _QUERY_KEYWORDS):
            return IntentResult(category=IntentCategory.PORTFOLIO_QUERY, confidence=0.8, allowed=True)

        if self.llm_client:
            return await self._classify_with_llm(user_message)

        return IntentResult(category=IntentCategory.GENERAL_QUESTION, confidence=0.5, allowed=True)

    async def _classify_with_llm(self, user_message: str) -> IntentResult:
        # TODO: call self.llm_client with a small classification prompt and
        # parse a category back out. Kept as a fallback path since most
        # traffic should hit the heuristics above.
        return IntentResult(category=IntentCategory.GENERAL_QUESTION, confidence=0.5, allowed=True)