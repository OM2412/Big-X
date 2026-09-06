import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from prometheus_client import Counter, Histogram
from web3 import Web3
from eth_account import Account

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_ABI_DIR = REPO_ROOT / "services" / "shared" / "abi"
SHARED_CONFIG_DIR = REPO_ROOT / "services" / "shared" / "config"


# ============================================================================
# EXCEPTIONS
# ============================================================================

class BlockchainClientError(Exception):
    """Base class for everything below."""


class ContractNotDeployedError(BlockchainClientError):
    def __init__(self, contract_name: str, network: str):
        self.contract_name = contract_name
        self.network = network
        super().__init__(f"{contract_name} has no recorded deployment on {network} — run scripts/sync_abis.py first")


class ProviderUnavailableError(BlockchainClientError):
    def __init__(self, network: str):
        super().__init__(f"No healthy RPC provider available for {network}")


class ContractCallError(BlockchainClientError):
    def __init__(self, contract_name: str, function_name: str, cause: Exception):
        self.cause = cause
        super().__init__(f"{contract_name}.{function_name} read failed: {cause}")


class ContractWriteError(BlockchainClientError):
    def __init__(self, contract_name: str, function_name: str, cause: Exception):
        self.cause = cause
        super().__init__(f"{contract_name}.{function_name} write failed before submission: {cause}")


class TransactionRevertedError(BlockchainClientError):
    def __init__(self, tx_hash: str, contract_name: str, function_name: str):
        self.tx_hash = tx_hash
        super().__init__(f"{contract_name}.{function_name} reverted on-chain: {tx_hash}")


class NonceError(BlockchainClientError):
    def __init__(self, address: str, cause: Exception):
        self.cause = cause
        super().__init__(f"Nonce resolution failed for {address}: {cause}")


class InputValidationError(BlockchainClientError):
    """Raised before a call/send ever reaches the network."""


# ============================================================================
# NONCE MANAGER — in-memory per-address counter for the small, fixed set
# of service operator keys. Not Redis-backed: these are single-process
# admin clients, not horizontally-scaled agent execution.
# ============================================================================

class NonceManager:
    def __init__(self):
        self._cache: dict[str, int] = {}

    def get_and_increment(self, w3: Web3, address: str) -> int:
        if address not in self._cache:
            self._cache[address] = w3.eth.get_transaction_count(address, "pending")
        nonce = self._cache[address]
        self._cache[address] += 1
        return nonce

    def resync(self, w3: Web3, address: str) -> None:
        self._cache[address] = w3.eth.get_transaction_count(address, "pending")
        logger.warning("Nonce resynced for %s to %d", address, self._cache[address])


# ============================================================================
# GAS STRATEGY — EIP-1559 fee estimation.
# ============================================================================

@dataclass
class GasEstimate:
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    gas_limit: int


class GasStrategy:
    def estimate(self, w3: Web3, contract_call, from_address: str, fallback_gas_limit: int, urgency: str = "standard") -> GasEstimate:
        try:
            gas_limit = int(contract_call.estimate_gas({"from": from_address}) * 1.2)
        except Exception:
            gas_limit = fallback_gas_limit

        fee_history = w3.eth.fee_history(20, "latest", [self._percentile_for(urgency)])
        next_base_fee = fee_history["baseFeePerGas"][-1]
        priority_fees = [r[0] for r in fee_history["reward"] if r]
        priority_fee = self._median(priority_fees) if priority_fees else w3.to_wei(1.5, "gwei")

        return GasEstimate(
            max_fee_per_gas=next_base_fee * 2 + priority_fee,
            max_priority_fee_per_gas=priority_fee,
            gas_limit=gas_limit,
        )

    def _percentile_for(self, urgency: str) -> int:
        return {"slow": 10, "standard": 50, "fast": 90}.get(urgency, 50)

    def _median(self, values: list[int]) -> int:
        s = sorted(values)
        mid = len(s) // 2
        return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) // 2


# ============================================================================
# METRICS — module-level Prometheus collectors.
# ============================================================================

BLOCKCHAIN_CLIENT_CALLS = Counter("blockchain_client_calls_total", "Contract calls", ["contract", "method", "kind", "result"])
BLOCKCHAIN_CLIENT_LATENCY = Histogram("blockchain_client_call_duration_seconds", "Call latency", ["contract", "method", "kind"])
BLOCKCHAIN_CLIENT_CIRCUIT_OPEN = Counter("blockchain_client_circuit_open_total", "Circuit breaker trips", ["contract"])


class MetricsCollector:
    def record_call(self, contract: str, method: str, kind: str, result: str, duration_seconds: float) -> None:
        BLOCKCHAIN_CLIENT_CALLS.labels(contract=contract, method=method, kind=kind, result=result).inc()
        BLOCKCHAIN_CLIENT_LATENCY.labels(contract=contract, method=method, kind=kind).observe(duration_seconds)

    def record_circuit_open(self, contract: str) -> None:
        BLOCKCHAIN_CLIENT_CIRCUIT_OPEN.labels(contract=contract).inc()


# ============================================================================
# CIRCUIT BREAKER
# ============================================================================

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
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

    def record_success(self) -> None:
        self._failures = 0
        self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        self._failures += 1
        if self._state == CircuitState.HALF_OPEN or self._failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    @property
    def state(self) -> CircuitState:
        return self._state


# ============================================================================
# CONTRACT CACHE — keyed by (provider identity, address) so a
# RpcProviderManager failover correctly invalidates stale bindings.
# ============================================================================

class ContractCache:
    def __init__(self):
        self._cache: dict[tuple[int, str], object] = {}

    def get_or_create(self, w3: Web3, address: str, abi: list):
        key = (id(w3), address)
        if key not in self._cache:
            self._cache[key] = w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)
        return self._cache[key]


# ============================================================================
# BASE CONTRACT CLIENT
# ============================================================================

class BaseContractClient:
    """NOT for agent-initiated tool calls (swap/bridge/defi/nft) — those
    go through WalletService.submit(), which signs via an agent's own
    session key/TBA. This class is for SERVICE-level admin calls made
    with the backend's own operator key."""

    def __init__(
        self, contract_name: str, network: str, rpc_provider_manager,
        operator_private_key: str, default_gas_limit: int = 400_000,
        max_retries: int = 3, base_backoff_seconds: float = 1.0,
        nonce_manager: NonceManager | None = None, gas_strategy: GasStrategy | None = None,
        metrics: MetricsCollector | None = None, contract_cache: ContractCache | None = None,
    ):
        self.contract_name = contract_name
        self.network = network
        self.rpc = rpc_provider_manager
        self.account = Account.from_key(operator_private_key)
        self.default_gas_limit = default_gas_limit
        self.max_retries = max_retries
        self.base_backoff_seconds = base_backoff_seconds

        self.nonces = nonce_manager or NonceManager()
        self.gas = gas_strategy or GasStrategy()
        self.metrics = metrics or MetricsCollector()
        self.contracts = contract_cache or ContractCache()
        self._circuit = CircuitBreaker()

        self.abi = self._load_abi()
        self.address = self._load_address()

    def _load_abi(self) -> list:
        with open(SHARED_ABI_DIR / f"{self.contract_name}.json") as f:
            return json.load(f)

    def _load_address(self) -> str:
        path = SHARED_CONFIG_DIR / f"deployed_addresses.{self.network}.json"
        if not path.exists():
            raise ContractNotDeployedError(self.contract_name, self.network)
        with open(path) as f:
            data = json.load(f)
        address = data.get("addresses", {}).get(self.contract_name)
        if address is None:
            raise ContractNotDeployedError(self.contract_name, self.network)
        return address

    def _get_provider_and_contract(self):
        if not self._circuit.allow():
            self.metrics.record_circuit_open(self.contract_name)
            raise ProviderUnavailableError(self.network)

        try:
            w3 = self.rpc.get_provider()
        except Exception as exc:
            self._circuit.record_failure()
            raise ProviderUnavailableError(self.network) from exc

        contract = self.contracts.get_or_create(w3, self.address, self.abi)
        return w3, contract

    def call(self, function_name: str, *args):
        start = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                w3, contract = self._get_provider_and_contract()
                fn = getattr(contract.functions, function_name)
                result = fn(*args).call()
                self._circuit.record_success()
                self.metrics.record_call(self.contract_name, function_name, "read", "success", time.monotonic() - start)
                return result
            except ProviderUnavailableError:
                raise
            except Exception as exc:
                last_error = exc
                self._circuit.record_failure()
                if attempt < self.max_retries:
                    time.sleep(self.base_backoff_seconds * (2 ** (attempt - 1)))

        self.metrics.record_call(self.contract_name, function_name, "read", "failure", time.monotonic() - start)
        raise ContractCallError(self.contract_name, function_name, last_error)

    def send(self, function_name: str, *args, value: int = 0, gas_limit: int | None = None) -> dict:
        start = time.monotonic()
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                result = self._send_once(function_name, args, value, gas_limit)
                self._circuit.record_success()
                self.metrics.record_call(self.contract_name, function_name, "write", "success", time.monotonic() - start)
                return result
            except TransactionRevertedError:
                self.metrics.record_call(self.contract_name, function_name, "write", "reverted", time.monotonic() - start)
                raise
            except ProviderUnavailableError:
                raise
            except Exception as exc:
                last_error = exc
                self._circuit.record_failure()
                if attempt < self.max_retries:
                    time.sleep(self.base_backoff_seconds * (2 ** (attempt - 1)))

        self.metrics.record_call(self.contract_name, function_name, "write", "failure", time.monotonic() - start)
        raise ContractWriteError(self.contract_name, function_name, last_error)

    def _send_once(self, function_name: str, args: tuple, value: int, gas_limit: int | None) -> dict:
        w3, contract = self._get_provider_and_contract()
        fn = getattr(contract.functions, function_name)
        call = fn(*args)

        try:
            nonce = self.nonces.get_and_increment(w3, self.account.address)
        except Exception as exc:
            raise NonceError(self.account.address, exc) from exc

        estimate = self.gas.estimate(w3, call, self.account.address, gas_limit or self.default_gas_limit)

        tx = call.build_transaction({
            "from": self.account.address,
            "value": value,
            "nonce": nonce,
            "gas": estimate.gas_limit,
            "maxFeePerGas": estimate.max_fee_per_gas,
            "maxPriorityFeePerGas": estimate.max_priority_fee_per_gas,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt.status != 1:
            raise TransactionRevertedError(tx_hash.hex(), self.contract_name, function_name)

        logger.info("%s.%s confirmed: %s", self.contract_name, function_name, tx_hash.hex())
        return {"tx_hash": tx_hash.hex(), "gas_used": receipt.gasUsed, "block_number": receipt.blockNumber}

    def get_event(self, event_name: str):
        _, contract = self._get_provider_and_contract()
        return getattr(contract.events, event_name)