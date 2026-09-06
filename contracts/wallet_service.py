import time
import logging
import asyncio
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum
from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
from prometheus_client import Counter, Histogram, Gauge
from redis.asyncio import Redis

logger = logging.getLogger(__name__)

TX_SUBMITTED = Counter("wallet_tx_submitted", "", ["status"])
TX_LATENCY = Histogram("wallet_tx_latency_seconds", "")
GAS_USED = Histogram("wallet_gas_used", "")
RPC_LATENCY = Histogram("wallet_rpc_latency_seconds", "", ["method"])
NONCE_GAP = Gauge("wallet_nonce_gap", "")
QUEUE_LEN = Gauge("wallet_queue_length", "")
RPC_FAILURES = Counter("wallet_rpc_failures", "", ["provider"])
REORG_COUNTER = Counter("wallet_reorgs", "")

class SessionKeyScope(str, Enum):
    FULL = "full"                  
    SWAP_ONLY = "swap_only"          
    READ_ONLY = "read_only"          


@dataclass
class SessionKeyGrant:
    agent_id: str
    session_public_key: str
    encrypted_session_private_key: str  # via encryption.py's EncryptionService
    scope: SessionKeyScope
    allowed_targets: set[str]             # empty set + scope=FULL means "any target"
    max_value_per_tx_wei: int
    expires_at: datetime
    owner_signature: str                   # the NFT owner's signature authorizing this grant
    revoked: bool = False


class SessionKeyExpiredError(Exception):
    pass


class SessionKeyScopeViolationError(Exception):
    pass


class SessionKeyManager:
    def __init__(self, encryption_service, db_session_factory):
        self.encryption_service = encryption_service
        self.db_session_factory = db_session_factory

    async def create_grant(
        self, agent_id: str, owner_address: str, scope: SessionKeyScope,
        allowed_targets: set[str], max_value_per_tx_wei: int, duration_hours: int,
        owner_signature: str, owner_signed_message: str,
    ) -> SessionKeyGrant:
        """Verifies the owner actually signed off on this delegation before
        creating it — this backend can generate a session key, but only the
        NFT owner's signature makes it a valid grant, not this service's say-so."""
        self._verify_owner_signature(owner_address, owner_signed_message, owner_signature)

        session_account = Account.create()
        encrypted_key = self.encryption_service.encrypt_string(
            session_account.key.hex(), associated_data=agent_id.encode(),
        )

        grant = SessionKeyGrant(
            agent_id=agent_id,
            session_public_key=session_account.address,
            encrypted_session_private_key=encrypted_key,
            scope=scope,
            allowed_targets=allowed_targets,
            max_value_per_tx_wei=max_value_per_tx_wei,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=duration_hours),
            owner_signature=owner_signature,
        )

        await self._persist(grant)
        logger.info("Session key created: agent=%s scope=%s expires=%s", agent_id, scope, grant.expires_at)
        return grant

    def _verify_owner_signature(self, owner_address: str, message: str, signature: str) -> None:
        encoded = encode_defunct(text=message)
        recovered = Account.recover_message(encoded, signature=signature)
        if recovered.lower() != owner_address.lower():
            raise PermissionError("Session key grant signature does not match agent owner")

    async def get_active_grant(self, agent_id: str) -> SessionKeyGrant | None:
        grant = await self._fetch(agent_id)
        if grant is None or grant.revoked:
            return None
        if datetime.now(timezone.utc) > grant.expires_at:
            return None
        return grant

    def check_scope(self, grant: SessionKeyGrant, target: str, value_wei: int) -> None:
        if datetime.now(timezone.utc) > grant.expires_at:
            raise SessionKeyExpiredError(f"Session key for agent {grant.agent_id} expired at {grant.expires_at}")

        if grant.scope == SessionKeyScope.READ_ONLY:
            raise SessionKeyScopeViolationError("Session key is read-only, cannot execute transactions")

        if value_wei > grant.max_value_per_tx_wei:
            raise SessionKeyScopeViolationError(
                f"Value {value_wei} exceeds session key's per-tx limit {grant.max_value_per_tx_wei}"
            )

        if grant.allowed_targets and Web3.to_checksum_address(target) not in grant.allowed_targets:
            raise SessionKeyScopeViolationError(f"Target {target} not in session key's allowed targets")

    async def revoke(self, agent_id: str) -> None:
        async with self.db_session_factory() as session:
            # TODO: UPDATE session_key_grants SET revoked = true WHERE agent_id = agent_id
            pass
        logger.warning("Session key revoked for agent %s", agent_id)

    async def _persist(self, grant: SessionKeyGrant) -> None:
        async with self.db_session_factory() as session:
            # TODO: insert into a session_key_grants table matching SessionKeyGrant's fields.
            pass

    async def _fetch(self, agent_id: str) -> SessionKeyGrant | None:
        async with self.db_session_factory() as session:
            # TODO: SELECT ... WHERE agent_id = agent_id AND NOT revoked ORDER BY created_at DESC LIMIT 1
            return None

class NonceManager:
    def __init__(self, w3: Web3, redis_client):
        self.w3 = w3
        self.redis = redis_client
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_and_increment(self, address: str) -> int:
        lock = self._locks.setdefault(address, asyncio.Lock())

        async with lock:
            key = f"nonce:{address}"
            cached = await self.redis.get(key)

            if cached is None:
                onchain_nonce = self.w3.eth.get_transaction_count(address, "pending")
                await self.redis.set(key, onchain_nonce, ex=3600)
                nonce = onchain_nonce
            else:
                nonce = int(cached)

            await self.redis.incr(key)
            return nonce

    async def resync_from_chain(self, address: str) -> None:
        onchain_nonce = self.w3.eth.get_transaction_count(address, "pending")
        await self.redis.set(f"nonce:{address}", onchain_nonce, ex=3600)
        logger.warning("Nonce resynced for %s to %d", address, onchain_nonce)

@dataclass
class GasEstimate:
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    gas_limit: int
    estimated_cost_wei: int


class GasEstimator:
    def __init__(self, w3: Web3):
        self.w3 = w3

    def estimate(self, to: str, value: int, data: bytes, from_address: str, urgency: str = "standard") -> GasEstimate:
        gas_limit = self.w3.eth.estimate_gas({"to": to, "value": value, "data": data, "from": from_address})
        gas_limit = int(gas_limit * 1.2)  # 20% headroom — on-chain state can shift between estimate and submission

        fee_history = self.w3.eth.fee_history(20, "latest", [self._percentile_for(urgency)])
        next_base_fee = fee_history["baseFeePerGas"][-1]

        priority_fees = [r[0] for r in fee_history["reward"] if r]
        priority_fee = self._median(priority_fees) if priority_fees else self.w3.to_wei(1.5, "gwei")

        max_fee_per_gas = next_base_fee * 2 + priority_fee

        return GasEstimate(
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=priority_fee,
            gas_limit=gas_limit,
            estimated_cost_wei=max_fee_per_gas * gas_limit,
        )

    def _percentile_for(self, urgency: str) -> int:
        return {"slow": 10, "standard": 50, "fast": 90}.get(urgency, 50)

    def _median(self, values: list[int]) -> int:
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        return sorted_values[mid] if len(sorted_values) % 2 else (sorted_values[mid - 1] + sorted_values[mid]) // 2

class TransactionStatus(str, Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FINALIZED = "finalized"
    FAILED = "failed"
    DROPPED = "dropped"
    REPLACED = "replaced"


@dataclass
class TransactionRecord:
    tx_hash: str
    agent_id: str
    status: TransactionStatus
    nonce: int
    created_at: datetime
    confirmations: int = 0
    retry_count: int = 0


class TransactionManager:

    def __init__(self, w3: Web3, db_session_factory):
        self.w3 = w3
        self.db_session_factory = db_session_factory

    async def record_pending(self, record: TransactionRecord):
        """
        Store transaction immediately after broadcasting.
        """
        async with self.db_session_factory() as session:
            # TODO: INSERT transaction into PostgreSQL
            pass

    async def update_confirmations(self, tx_hash: str):
        """
        Update confirmation count.
        """
        receipt = self.w3.eth.get_transaction_receipt(tx_hash)
        latest = self.w3.eth.block_number
        confirmations = latest - receipt.blockNumber + 1

        # TODO: UPDATE confirmations in database

        return confirmations

    async def mark_finalized(self, tx_hash: str):
        """
        Mark transaction finalized after required confirmations.
        """
        # TODO: UPDATE status = FINALIZED
        pass

    async def retry_transaction(self, tx_hash: str):
        """
        Retry dropped or failed transaction.
        """
        # TODO: Implement retry logic
        pass

    async def detect_replacement(self, tx_hash: str):
        """
        Detect nonce replacement.
        """
        # TODO: Check if another tx used same nonce
        pass

class RPCProvider:

    def __init__(self, name: str, endpoint: str):
        self.name = name
        self.endpoint = endpoint
        self.healthy = True
        self.latency = 0
        self.last_checked = None


class RPCHealthManager:

    def __init__(self, providers: list[RPCProvider]):
        self.providers = providers

    async def health_check(self):
        for provider in self.providers:
            start = time.time()
            try:
                w3 = Web3(Web3.HTTPProvider(provider.endpoint))
                w3.eth.block_number
                provider.healthy = True
            except Exception:
                provider.healthy = False
            provider.latency = time.time() - start
            provider.last_checked = datetime.utcnow()

    def get_best_provider(self):
        healthy = [p for p in self.providers if p.healthy]
        if not healthy:
            raise Exception("No healthy RPC provider available")
        healthy.sort(key=lambda p: p.latency)
        return healthy[0]

    async def failover(self):
        await self.health_check()
        return self.get_best_provider()


class TxPriority(Enum):
    HIGH = 0
    NORMAL = 1
    LOW = 2


@dataclass(order=True)
class QueuedTx:
    priority: TxPriority
    sequence: int = field(compare=False)
    agent_id: str = field(compare=False)
    target: str = field(compare=False)
    value: int = field(compare=False)
    calldata: bytes = field(compare=False)
    created_at: datetime = field(default_factory=datetime.utcnow, compare=False)


class TransactionQueue:
    def __init__(self, max_concurrent: int = 5):
        self._heap: List[QueuedTx] = []
        self._counter = 0
        self._sem = asyncio.Semaphore(max_concurrent)

    async def enqueue(self, agent_id: str, target: str, value: int, calldata: bytes, priority: TxPriority = TxPriority.NORMAL):
        self._counter += 1
        item = QueuedTx(priority, self._counter, agent_id, target, value, calldata)
        heapq.heappush(self._heap, item)
        QUEUE_LEN.set(len(self._heap))
        await self._sem.acquire()
        return heapq.heappop(self._heap)

    def release(self):
        self._sem.release()

    def size(self):
        return len(self._heap)


class ConfirmationWorker:
    def __init__(self, w3, tx_manager, required_conf: int = 12, interval: int = 12):
        self.w3 = w3
        self.tx_manager = tx_manager
        self.required_conf = required_conf
        self.interval = interval
        self._running = False

    async def start(self):
        self._running = True
        while self._running:
            try:
                pending = await self.tx_manager.get_pending()
                for tx in pending:
                    try:
                        receipt = self.w3.eth.get_transaction_receipt(tx["tx_hash"])
                        if receipt:
                            conf = self.w3.eth.block_number - receipt.blockNumber + 1
                            if conf >= self.required_conf:
                                await self.tx_manager.mark_finalized(tx["tx_hash"])
                                TX_LATENCY.observe(time.time() - tx.get("submitted_at", time.time()))
                            else:
                                await self.tx_manager.update_confirmations(tx["tx_hash"])
                    except Exception as e:
                        logger.exception(f"Confirmation worker error: {e}")
                        await self.tx_manager.mark_dropped(tx["tx_hash"])
            except Exception as e:
                logger.exception(f"Worker loop error: {e}")
            await asyncio.sleep(self.interval)

    async def stop(self):
        self._running = False


class ReorgHandler:
    def __init__(self, w3, tx_manager, redis_client: Redis):
        self.w3 = w3
        self.tx_manager = tx_manager
        self.redis = redis_client
        self._last_block = None

    async def check(self):
        try:
            current = self.w3.eth.get_block("latest")
            if self._last_block:
                if current.get("parentHash") != self._last_block.get("hash"):
                    affected = await self.tx_manager.get_confirmed_after(self._last_block.get("number"))
                    for tx in affected:
                        await self.tx_manager.mark_pending(tx["tx_hash"])
                    cursor = 0
                    keys = []
                    while True:
                        cursor, batch = await self.redis.scan(cursor, match="nonce:*")
                        keys.extend(batch)
                        if cursor == 0:
                            break
                    if keys:
                        await self.redis.delete(*keys)
                    REORG_COUNTER.inc()
                    logger.warning(f"Reorg detected: {self._last_block['number']} -> {current['number']}")
            self._last_block = current
        except Exception as e:
            logger.exception(f"Reorg check failed: {e}")


class RPCLoadBalancer:
    def __init__(self, providers: List[Dict]):
        self.providers = [{**p, "w3": None, "healthy": True} for p in providers]
        self._index = 0
        self._lock = asyncio.Lock()

    async def health_check(self):
        for p in self.providers:
            try:
                if not p["w3"]:
                    p["w3"] = Web3(Web3.HTTPProvider(p["endpoint"]))
                p["w3"].eth.block_number
                p["healthy"] = True
            except Exception as e:
                p["healthy"] = False
                RPC_FAILURES.labels(provider=p.get("name", "unknown")).inc()
                logger.warning(f"RPC {p.get('name')} unhealthy: {e}")

    async def get_w3(self):
        await self.health_check()
        healthy = [p for p in self.providers if p["healthy"]]
        if not healthy:
            raise Exception("No healthy RPC providers")
        async with self._lock:
            if self._index >= len(healthy):
                self._index = 0
            p = healthy[self._index]
            self._index += 1
            return p["w3"]


class Metrics:
    @staticmethod
    def record_submit(status: str):
        TX_SUBMITTED.labels(status=status).inc()

    @staticmethod
    def record_gas(gas: int):
        GAS_USED.observe(gas)

    @staticmethod
    def record_rpc(method: str, latency: float):
        RPC_LATENCY.labels(method=method).observe(latency)

    @staticmethod
    def set_nonce_gap(gap: int):
        NONCE_GAP.set(gap)


@dataclass
class SignedSubmission:
    tx_hash: str
    nonce: int
    gas_estimate: GasEstimate
    signer: str 

class WalletService:
    def __init__(
        self,
        w3,
        session_key_manager,
        nonce_manager,
        gas_estimator,
        transaction_manager,
        rpc_manager,
        encryption_service,
        mpc_wallet_client,
        redis_client: Optional[Redis] = None,
        rpc_providers: Optional[List[Dict]] = None,
    ):
        self.w3 = w3
        self.session_keys = session_key_manager
        self.nonces = nonce_manager
        self.gas = gas_estimator
        self.transactions = transaction_manager
        self.rpc_manager = rpc_manager
        self.encryption_service = encryption_service
        self.mpc_wallet = mpc_wallet_client

        self.tx_queue = TransactionQueue()
        self.confirmation_worker = ConfirmationWorker(w3, transaction_manager)
        self.reorg_handler = ReorgHandler(w3, transaction_manager, redis_client) if redis_client else None
        self.rpc_balancer = RPCLoadBalancer(rpc_providers or []) if rpc_providers else None

    async def submit(self, agent_id: str, target: str, value: int, calldata: bytes) -> SignedSubmission:
        grant = await self.session_keys.get_active_grant(agent_id)

        if grant is not None:
            return await self._submit_with_session_key(grant, target, value, calldata)

        logger.info("No active session key for agent %s, falling back to MPC wallet", agent_id)
        return await self._submit_via_mpc(agent_id, target, value, calldata)

    async def _submit_with_session_key(self, grant: SessionKeyGrant, target: str, value: int, calldata: bytes) -> SignedSubmission:
        self.session_keys.check_scope(grant, target, value)

        queued = await self.tx_queue.enqueue(grant.agent_id, target, value, calldata)

        private_key = self.encryption_service.decrypt_string(
            grant.encrypted_session_private_key, associated_data=grant.agent_id.encode(),
        )
        account = Account.from_key(private_key)

        nonce = await self.nonces.get_and_increment(account.address)
        gas_estimate = self.gas.estimate(target, value, calldata, account.address)

        tx = {
            "to": target, "value": value, "data": calldata, "nonce": nonce,
            "maxFeePerGas": gas_estimate.max_fee_per_gas,
            "maxPriorityFeePerGas": gas_estimate.max_priority_fee_per_gas,
            "gas": gas_estimate.gas_limit,
            "chainId": self.w3.eth.chain_id,
            "type": 2,
        }
        signed = account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)

        await self.transactions.record_pending(
            TransactionRecord(
                tx_hash=tx_hash.hex(),
                agent_id=grant.agent_id,
                status=TransactionStatus.PENDING,
                nonce=nonce,
                created_at=datetime.utcnow(),
            )
        )
        Metrics.record_submit("submitted")

        private_key = None
        self.tx_queue.release()

        return SignedSubmission(tx_hash=tx_hash.hex(), nonce=nonce, gas_estimate=gas_estimate, signer=account.address)

    async def _submit_via_mpc(self, agent_id: str, target: str, value: int, calldata: bytes) -> SignedSubmission:
        signed_tx = await self.mpc_wallet.sign_and_send(
            wallet_address=agent_id, to=target, value=value, data=calldata, chain="base",
        )
        return SignedSubmission(
            tx_hash=signed_tx.tx_hash, nonce=-1,  # MPC wallet manages its own nonce internally
            gas_estimate=GasEstimate(0, 0, 0, 0),  # not exposed by this path — see mpc_wallet.py's TODO on CDP SDK integration
            signer=signed_tx.wallet_address,
        )