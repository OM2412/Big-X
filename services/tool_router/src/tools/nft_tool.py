import time
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
 
from prometheus_client import Counter, Histogram
from sqlalchemy import Column, String, Numeric, DateTime, Boolean, Text
from web3 import Web3
from web3.types import ChecksumAddress
 
from tool import Tool, ToolCall
 
logger = logging.getLogger(__name__)
 
 
# ---------------------------------------------------------------------------
# Prometheus metrics — module-level so they're registered once per process,
# not per NFTTool instance (multiple instances, e.g. in tests, would
# otherwise raise a duplicate-registration error).
# ---------------------------------------------------------------------------
 
NFT_TOOL_CALLS = Counter(
    "nft_tool_calls_total", "Total nft_tool invocations", ["action", "result"]
)
NFT_TOOL_DURATION = Histogram(
    "nft_tool_call_duration_seconds", "nft_tool build_call latency", ["action"]
)
NFT_TOOL_BLOCKED = Counter(
    "nft_tool_blocked_total", "Blocked nft_tool attempts", ["reason"]
)
NFT_TOOL_VALUE_WEI = Histogram(
    "nft_tool_trade_value_wei", "Value of nft_tool trades in wei", ["action"],
    buckets=(1e15, 1e16, 1e17, 1e18, 5e18, 1e19, 5e19, 1e20, float("inf")),
)
 
 
# ---------------------------------------------------------------------------
# PostgreSQL audit trail. Defined here per your request to keep this
# self-contained in nft_tool.py — in a larger migration pass you'd likely
# want this alongside db/models/, using the same Base/TimestampMixin as
# your other tables, which is exactly what it does below.
# ---------------------------------------------------------------------------
 
try:
    from db.base import Base, TimestampMixin
except ImportError:
    # Fallback so this file can be imported/tested standalone without your
    # full db package on the path — real deployments should hit the try branch.
    from sqlalchemy.orm import DeclarativeBase
 
    class Base(DeclarativeBase):
        pass
 
    class TimestampMixin:
        pass
 
 
class NFTToolAuditLog(Base, TimestampMixin):
    __tablename__ = "nft_tool_audit_log"
 
    id = Column(String(36), primary_key=True)
    correlation_id = Column(String(36), index=True)
    agent_id = Column(String(78), index=True)  # uint256 as string — nft_id can exceed int64
    user_id = Column(String(36), index=True)
    action = Column(String(20))                 # buy | list | cancel
    target_nft_id = Column(String(78))
    counterparty_address = Column(String(42), nullable=True)
    price_wei = Column(Numeric(78, 0), nullable=True)
    allowed = Column(Boolean)
    block_reason = Column(Text, nullable=True)
    tx_target = Column(String(42), nullable=True)
 
 
# ---------------------------------------------------------------------------
# Security validation
# ---------------------------------------------------------------------------
 
class NFTToolBlockedError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)
 
 
@dataclass
class SecurityConfig:
    blocked_nft_ids: set[int]              # specific agents frozen from trading (fraud hold, compliance, dispute)
    address_blacklist: set[str]             # sanctioned/banned addresses — checked as buyer AND seller
    max_reasonable_price_wei: int            # soft ceiling — doesn't block, but flags for review/notification
 
 
# ---------------------------------------------------------------------------
# Main tool
# ---------------------------------------------------------------------------
 
class NFTTool(Tool):
    name = "nft_tool"
 
    def __init__(
        self,
        marketplace_contract,
        policy_engine_client,
        spend_limits_service,
        notification_dispatcher,
        db_session_factory,
        security_config: SecurityConfig,
    ):
        self.contract = marketplace_contract
        self.policy_engine_client = policy_engine_client
        self.spend_limits_service = spend_limits_service
        self.notification_dispatcher = notification_dispatcher
        self.db_session_factory = db_session_factory
        self.security_config = security_config
 
    async def build_call(
        self,
        action: str,
        params: dict,
        agent_id: str,
        user_id: str,
        correlation_id: str | None = None,
    ) -> ToolCall:
        correlation_id = correlation_id or str(uuid.uuid4())
        start = time.monotonic()
        log_ctx = {"agent_id": agent_id, "action": action, "correlation_id": correlation_id, "tool": "nft_tool"}
 
        logger.info("nft_tool: build_call started", extra=log_ctx)
 
        try:
            self._check_action_supported(action)
            self._check_blocklists(action, params, log_ctx)
            await self._check_policy_and_spend_limits(action, params, agent_id, log_ctx)
 
            tool_call = self._build_action_call(action, params)
 
            await self._audit(
                correlation_id, agent_id, user_id, action, params, allowed=True,
                block_reason=None, tx_target=tool_call.target,
            )
            await self._maybe_notify_high_value(action, params, user_id, correlation_id)
 
            NFT_TOOL_CALLS.labels(action=action, result="success").inc()
            NFT_TOOL_VALUE_WEI.labels(action=action).observe(tool_call.value or params.get("price_wei", 0))
            logger.info("nft_tool: build_call succeeded", extra={**log_ctx, "target": tool_call.target})
            return tool_call
 
        except NFTToolBlockedError as exc:
            NFT_TOOL_CALLS.labels(action=action, result="blocked").inc()
            NFT_TOOL_BLOCKED.labels(reason=exc.reason).inc()
            await self._audit(correlation_id, agent_id, user_id, action, params, allowed=False, block_reason=exc.reason, tx_target=None)
            await self._notify_blocked(action, params, user_id, exc.reason, correlation_id)
            logger.warning("nft_tool: blocked — %s", exc.reason, extra=log_ctx)
            raise
 
        except Exception:
            NFT_TOOL_CALLS.labels(action=action, result="error").inc()
            logger.exception("nft_tool: build_call failed unexpectedly", extra=log_ctx)
            raise
 
        finally:
            NFT_TOOL_DURATION.labels(action=action).observe(time.monotonic() - start)
 
    # -- Validation ---------------------------------------------------------
 
    def _check_action_supported(self, action: str) -> None:
        if action not in ("buy", "list", "cancel"):
            raise ValueError(f"Unsupported nft_tool action: {action}")
 
    def _check_blocklists(self, action: str, params: dict, log_ctx: dict) -> None:
        nft_id = int(params["nft_id"])
        if nft_id in self.security_config.blocked_nft_ids:
            raise NFTToolBlockedError(f"NFT {nft_id} is on the trading-blocked list")
 
        addresses_to_check = []
        if action == "buy":
            addresses_to_check.append(params.get("buyer_address"))
        if action in ("buy", "list"):
            addresses_to_check.append(params.get("seller_address"))
 
        for address in filter(None, addresses_to_check):
            checksum = Web3.to_checksum_address(address)
            if checksum in self.security_config.address_blacklist:
                raise NFTToolBlockedError(f"Address {checksum} is on the blocklist")
 
    async def _check_policy_and_spend_limits(self, action: str, params: dict, agent_id: str, log_ctx: dict) -> None:
        if action != "buy":
            return  # only spends move value — list/cancel don't touch PolicyEngine or spend limits
 
        price_wei = int(params["price_wei"])
        target = self.contract.address
        calldata = self.contract.encodeABI(fn_name="buy", args=[int(params["nft_id"])])
 
        allowed, reason = await self.policy_engine_client.check_action(
            agent_id=agent_id, target=target, value=price_wei, data=Web3.to_bytes(hexstr=calldata),
        )
        if not allowed:
            raise NFTToolBlockedError(f"PolicyEngine rejected: {reason}")
 
        amount_usd = params.get("amount_usd")
        if amount_usd is not None:
            spend_result = await self.spend_limits_service.check(agent_id, amount_usd)
            if not spend_result.allowed:
                raise NFTToolBlockedError(f"SpendLimits rejected: {spend_result.reason}")
 
    def _build_action_call(self, action: str, params: dict) -> ToolCall:
        if action == "buy":
            return self._build_buy(params)
        if action == "list":
            return self._build_list(params)
        return self._build_cancel(params)
 
    def _build_buy(self, params: dict) -> ToolCall:
        nft_id = params["nft_id"]
        price_wei = params["price_wei"]
        calldata = self.contract.encodeABI(fn_name="buy", args=[nft_id])
        return ToolCall(target=self.contract.address, value=price_wei, calldata=Web3.to_bytes(hexstr=calldata))
 
    def _build_list(self, params: dict) -> ToolCall:
        # NOTE: requires the NFT already approved to the marketplace contract —
        # that approve() call must happen first (same caveat as defi_tool's
        # deposit flow — see that file's DeFiCallPlan for the pattern this
        # tool should eventually adopt too).
        calldata = self.contract.encodeABI(fn_name="list", args=[params["nft_id"], params["price_wei"]])
        return ToolCall(target=self.contract.address, value=0, calldata=Web3.to_bytes(hexstr=calldata))
 
    def _build_cancel(self, params: dict) -> ToolCall:
        calldata = self.contract.encodeABI(fn_name="cancelListing", args=[params["nft_id"]])
        return ToolCall(target=self.contract.address, value=0, calldata=Web3.to_bytes(hexstr=calldata))
 
    # -- Audit trail ----------------------------------------------------------
 
    async def _audit(
        self, correlation_id: str, agent_id: str, user_id: str, action: str, params: dict,
        allowed: bool, block_reason: str | None, tx_target: str | None,
    ) -> None:
        entry = NFTToolAuditLog(
            id=str(uuid.uuid4()),
            correlation_id=correlation_id,
            agent_id=str(agent_id),
            user_id=user_id,
            action=action,
            target_nft_id=str(params.get("nft_id", "")),
            counterparty_address=params.get("seller_address") or params.get("buyer_address"),
            price_wei=params.get("price_wei"),
            allowed=allowed,
            block_reason=block_reason,
            tx_target=tx_target,
        )
        try:
            async with self.db_session_factory() as session:
                session.add(entry)
                await session.commit()
        except Exception:
            # Audit failures should never block the actual trade decision —
            # log loudly so it's visible in monitoring, but don't raise.
            logger.exception("nft_tool: failed to write audit log entry, continuing")
 
    # -- Notifications ----------------------------------------------------
 
    async def _notify_blocked(self, action: str, params: dict, user_id: str, reason: str, correlation_id: str) -> None:
        from services.notification_service.src.dispatcher import Notification, NotificationType  # your existing dispatcher types
        await self.notification_dispatcher.dispatch(Notification(
            type=NotificationType.EXECUTION_FAILED,
            recipient_user_id=user_id,
            subject="NFT trade blocked",
            body=f"Your {action} request for NFT {params.get('nft_id')} was blocked: {reason}",
        ))
 
    async def _maybe_notify_high_value(self, action: str, params: dict, user_id: str, correlation_id: str) -> None:
        if action != "buy":
            return
        price_wei = int(params.get("price_wei", 0))
        if price_wei < self.security_config.max_reasonable_price_wei:
            return
 
        from services.notification_service.src.dispatcher import Notification, NotificationType
        await self.notification_dispatcher.dispatch(Notification(
            type=NotificationType.MARKETPLACE_SALE,
            recipient_user_id=user_id,
            subject="High-value NFT purchase pending",
            body=f"Agent is about to buy NFT {params.get('nft_id')} for {price_wei} wei — above your review threshold.",
        ))