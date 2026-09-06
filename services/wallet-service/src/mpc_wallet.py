

import os
import time
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)

CDP_API_KEY_NAME = os.environ["CDP_API_KEY_NAME"]
CDP_API_KEY_PRIVATE_KEY = os.environ["CDP_API_KEY_PRIVATE_KEY"]  # TODO: secrets manager before mainnet


class PermanentFailure(Exception):
    pass


class TransientFailure(Exception):
    pass


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, recovery_timeout_seconds: float = 30.0):
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self._failures = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self.recovery_timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                return True
            return False
        return True

    def record_success(self):
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self):
        self._failures += 1
        if self._state == CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            logger.error("MPC wallet circuit breaker OPEN after %d failures", self._failures)


@dataclass
class SignedTransaction:
    tx_hash: str
    wallet_address: str
    chain: str


class MpcWalletClient:
    """CDP's actual Python SDK API surface changes between versions —
    verify method names (Wallet.create, wallet.invoke_contract, etc.)
    against the current CDP SDK docs before relying on this signature."""

    def __init__(self, max_retries: int = 3, base_backoff_seconds: float = 1.0):
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds
        self._circuit = _CircuitBreaker()
        self._client = self._init_cdp_client()

    def _init_cdp_client(self):
        # TODO: from cdp import Cdp, Wallet
        #   Cdp.configure(api_key_name=CDP_API_KEY_NAME, private_key=CDP_API_KEY_PRIVATE_KEY)
        #   return Cdp
        raise NotImplementedError("Wire up the real CDP SDK client per current cdp-sdk docs")

    async def create_wallet(self, network_id: str) -> str:
        """Provisions a new CDP-managed MPC wallet for an agent. Returns
        the wallet's address. Called once during agent provisioning,
        alongside (or instead of, depending on your final architecture)
        the ERC-6551 Token Bound Account."""
        return await self._with_resilience(self._create_wallet_impl, network_id)

    async def _create_wallet_impl(self, network_id: str) -> str:
        # TODO: wallet = self._client.Wallet.create(network_id=network_id)
        #       return wallet.default_address.address_id
        raise NotImplementedError

    async def sign_and_send(self, wallet_address: str, to: str, value: int, data: bytes, chain: str) -> SignedTransaction:
        return await self._with_resilience(self._sign_and_send_impl, wallet_address, to, value, data, chain)

    async def _sign_and_send_impl(self, wallet_address: str, to: str, value: int, data: bytes, chain: str) -> SignedTransaction:
        # TODO: wallet = self._client.Wallet.fetch(wallet_address)
        #       tx = wallet.invoke_contract(contract_address=to, amount=value, data=data, ...)
        #       tx.wait()
        #       return SignedTransaction(tx_hash=tx.transaction_hash, wallet_address=wallet_address, chain=chain)
        raise NotImplementedError

    async def get_balance(self, wallet_address: str, asset_id: str) -> float:
        return await self._with_resilience(self._get_balance_impl, wallet_address, asset_id)

    async def _get_balance_impl(self, wallet_address: str, asset_id: str) -> float:
        # TODO: wallet = self._client.Wallet.fetch(wallet_address)
        #       return float(wallet.balance(asset_id))
        raise NotImplementedError

    async def _with_resilience(self, fn, *args):
        if not self._circuit.allow():
            raise TransientFailure("MPC wallet circuit breaker open — CDP likely degraded")

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                result = await fn(*args)
                self._circuit.record_success()
                return result
            except PermanentFailure:
                self._circuit.record_failure()
                raise
            except Exception as exc:
                last_error = exc
                self._circuit.record_failure()
                if attempt < self.max_retries and self._circuit.allow():
                    backoff = self.base_backoff_seconds * (2 ** (attempt - 1))
                    logger.warning("MPC wallet call failed (attempt %d/%d), retrying in %.1fs: %s",
                                   attempt, self.max_retries, backoff, exc)
                    await asyncio.sleep(backoff)

        raise TransientFailure(f"MPC wallet call failed after {self.max_retries} attempts: {last_error}")