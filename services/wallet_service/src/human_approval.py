
import uuid
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

logger = logging.getLogger(__name__)

DEFAULT_APPROVAL_EXPIRY_MINUTES = 30


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


@dataclass
class ApprovalRequest:
    id: str
    agent_id: str
    user_id: str
    target: str
    value: int
    calldata: bytes
    amount_usd: float
    description: str
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    action_hash: bytes  # matches PolicyEngine.sol's keccak256(agent_id, target, value, data)


class ApprovalExpiredError(Exception):
    pass


class ApprovalNotFoundError(Exception):
    pass


class HumanApprovalService:
    def __init__(self, db_session_factory, notification_dispatcher, policy_engine_client, w3):
        self.db_session_factory = db_session_factory
        self.notification_dispatcher = notification_dispatcher
        self.policy_engine_client = policy_engine_client
        self.w3 = w3

    async def request_approval(
        self, agent_id: str, user_id: str, target: str, value: int, calldata: bytes,
        amount_usd: float, description: str, expiry_minutes: int = DEFAULT_APPROVAL_EXPIRY_MINUTES,
    ) -> ApprovalRequest:
        action_hash = self._compute_action_hash(agent_id, target, value, calldata)

        request = ApprovalRequest(
            id=str(uuid.uuid4()),
            agent_id=agent_id,
            user_id=user_id,
            target=target,
            value=value,
            calldata=calldata,
            amount_usd=amount_usd,
            description=description,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=expiry_minutes),
            action_hash=action_hash,
        )

        await self._persist(request)

        from .channels_types import Notification, NotificationType  # your notification-service types
        await self.notification_dispatcher.dispatch(Notification(
            type=NotificationType.HUMAN_APPROVAL_NEEDED,
            recipient_user_id=user_id,
            subject="Action needs your approval",
            body=f"{description} (${amount_usd:,.2f}). Expires in {expiry_minutes} minutes.",
        ))

        logger.info("Approval requested: id=%s agent=%s amount_usd=%.2f", request.id, agent_id, amount_usd)
        return request

    async def approve(self, request_id: str, approving_user_id: str) -> ApprovalRequest:
        request = await self._get(request_id)

        if request.user_id != approving_user_id:
            raise PermissionError("Only the requesting user can approve this action")

        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"Cannot approve a request in status: {request.status}")

        if datetime.now(timezone.utc) > request.expires_at:
            await self._update_status(request_id, ApprovalStatus.EXPIRED)
            raise ApprovalExpiredError(f"Approval request {request_id} expired at {request.expires_at}")

        # Submit on-chain — this is what actually unblocks PolicyEngine.checkAction
        # for the Executor's next attempt at this exact action.
        self.policy_engine_client.approve_action(int(request.agent_id), request.action_hash)

        await self._update_status(request_id, ApprovalStatus.APPROVED)
        logger.info("Approval granted: id=%s", request_id)

        request.status = ApprovalStatus.APPROVED
        return request

    async def reject(self, request_id: str, rejecting_user_id: str) -> ApprovalRequest:
        request = await self._get(request_id)
        if request.user_id != rejecting_user_id:
            raise PermissionError("Only the requesting user can reject this action")

        await self._update_status(request_id, ApprovalStatus.REJECTED)
        request.status = ApprovalStatus.REJECTED
        return request

    async def expire_stale_requests(self) -> int:
        """Run on a schedule (e.g. every minute) to sweep PENDING requests
        past their expires_at — without this, a request a user never
        responds to would sit PENDING forever."""
        async with self.db_session_factory() as session:
            # TODO: UPDATE approval_requests SET status = 'expired'
            #       WHERE status = 'pending' AND expires_at < now()
            #       RETURNING id — return the count.
            return 0

    def _compute_action_hash(self, agent_id: str, target: str, value: int, calldata: bytes) -> bytes:
        # Must match PolicyEngine.sol's keccak256(abi.encode(nftId, target, value, data))
        # EXACTLY, or on-chain approveAction() will never match what checkAction()
        # looks up. Solidity's keccak256 is NOT the same algorithm as Python's
        # hashlib.sha3_256 (that's NIST SHA3, a different finalization) — Web3.keccak
        # implements the original Keccak used by Ethereum, which is what's required here.
        from eth_abi import encode
        from web3 import Web3
        encoded = encode(["uint256", "address", "uint256", "bytes"], [int(agent_id), target, value, calldata])
        return Web3.keccak(encoded)

    async def _persist(self, request: ApprovalRequest) -> None:
        async with self.db_session_factory() as session:
            # TODO: insert into an approval_requests table matching ApprovalRequest's fields.
            pass

    async def _get(self, request_id: str) -> ApprovalRequest:
        async with self.db_session_factory() as session:
            # TODO: SELECT ... WHERE id = request_id; raise ApprovalNotFoundError if missing.
            raise ApprovalNotFoundError(f"No approval request found: {request_id}")

    async def _update_status(self, request_id: str, status: ApprovalStatus) -> None:
        async with self.db_session_factory() as session:
            # TODO:
            pass