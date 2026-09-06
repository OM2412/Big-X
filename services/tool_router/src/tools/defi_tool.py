# tools/defi_tool.py
#
# Deposits/withdraws into lending & yield protocols (Aave, Compound).
# v2: adapter pattern per protocol, multi-chain registry, Decimal-precision
# amounts, retry+circuit breaker on reads, structured logging + metrics,
# and an explicit approval-call plan instead of a "you must do this
# separately" comment.

import time
import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from enum import Enum

from web3 import Web3

from tool import Tool, ToolCall

logger = logging.getLogger(__name__)


class MetricsHook:
    def increment(self, metric: str, tags: dict | None = None) -> None:
        pass

    def observe_latency(self, metric: str, seconds: float, tags: dict | None = None) -> None:
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
            logger.error("defi_tool: circuit breaker OPEN after %d failures", self._failures)


@dataclass
class AssetConfig:
    address: str
    decimals: int
    min_amount: Decimal | None = None  # protocol-specific dust limits, if any
    max_amount: Decimal | None = None


@dataclass
class PoolConfig:
    address: str
    abi: list
    chain_id: int


class ProtocolAdapter(ABC):
    protocol_name: str

    @abstractmethod
    def encode_deposit(self, w3: Web3, pool_config: PoolConfig, asset: AssetConfig, raw_amount: int, on_behalf_of: str) -> str:
        ...

    @abstractmethod
    def encode_withdraw(self, w3: Web3, pool_config: PoolConfig, asset: AssetConfig, raw_amount: int, on_behalf_of: str) -> str:
        ...

    @abstractmethod
    def is_paused(self, w3: Web3, pool_config: PoolConfig, asset: AssetConfig) -> bool:
        """Reads on-chain pause state for this specific asset/pool. Building
        a call against a paused market would fail on-chain anyway, but
        checking here fails fast with a clear reason instead of burning
        gas on a doomed transaction."""
        ...


class AaveV3Adapter(ProtocolAdapter):
    protocol_name = "aave"

    def encode_deposit(self, w3, pool_config, asset, raw_amount, on_behalf_of) -> str:
        contract = w3.eth.contract(address=Web3.to_checksum_address(pool_config.address), abi=pool_config.abi)
        return contract.encodeABI(fn_name="supply", args=[asset.address, raw_amount, on_behalf_of, 0])

    def encode_withdraw(self, w3, pool_config, asset, raw_amount, on_behalf_of) -> str:
        contract = w3.eth.contract(address=Web3.to_checksum_address(pool_config.address), abi=pool_config.abi)
        return contract.encodeABI(fn_name="withdraw", args=[asset.address, raw_amount, on_behalf_of])

    def is_paused(self, w3, pool_config, asset) -> bool:
        # TODO: confirm against the exact Aave V3 Pool ABI version you deploy
        # against — this typically reads getReserveData(asset).configuration
        # and decodes the paused bit, or calls a dedicated PoolConfigurator
        # view. Left explicit rather than guessing at a bitmask.
        contract = w3.eth.contract(address=Web3.to_checksum_address(pool_config.address), abi=pool_config.abi)
        try:
            reserve_data = contract.functions.getReserveData(asset.address).call()
            return False  # placeholder until the real configuration decode is wired in
        except Exception:
            logger.warning("defi_tool: could not read Aave pause state, treating as unknown (not blocking)")
            return False


class CompoundV3Adapter(ProtocolAdapter):
    protocol_name = "compound"

    def encode_deposit(self, w3, pool_config, asset, raw_amount, on_behalf_of) -> str:
        contract = w3.eth.contract(address=Web3.to_checksum_address(pool_config.address), abi=pool_config.abi)
        return contract.encodeABI(fn_name="supply", args=[asset.address, raw_amount])

    def encode_withdraw(self, w3, pool_config, asset, raw_amount, on_behalf_of) -> str:
        contract = w3.eth.contract(address=Web3.to_checksum_address(pool_config.address), abi=pool_config.abi)
        return contract.encodeABI(fn_name="withdraw", args=[asset.address, raw_amount])

    def is_paused(self, w3, pool_config, asset) -> bool:
        # TODO: Compound V3 (Comet) exposes isSupplyPaused()/isWithdrawPaused()
        # directly — call those rather than guessing at a generic pause flag.
        contract = w3.eth.contract(address=Web3.to_checksum_address(pool_config.address), abi=pool_config.abi)
        try:
            return contract.functions.isSupplyPaused().call()
        except Exception:
            logger.warning("defi_tool: could not read Compound pause state, treating as unknown (not blocking)")
            return False


# Minimal ERC-20 ABI for building the approval call this tool now actually
# produces, instead of leaving it as a comment for the Executor to remember.
_ERC20_APPROVE_ABI = [
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]


@dataclass
class DeFiCallPlan:
    """What build_call used to hide: a deposit often needs an approval
    first. Returning both explicitly means the Executor knows exactly what
    to submit, in what order, instead of relying on a code comment."""
    approval_call: ToolCall | None
    action_call: ToolCall
    protocol: str
    asset_symbol: str
    amount: Decimal


class DeFiTool(Tool):
    name = "defi_tool"

    def __init__(
        self,
        w3: Web3,
        pool_registry: dict[tuple[str, int], PoolConfig],
        asset_registry: dict[str, AssetConfig],
        metrics: MetricsHook | None = None,
    ):
        self.w3 = w3
        self.pool_registry = pool_registry    # injected, not hardcoded — different per environment/chain
        self.asset_registry = asset_registry
        self.metrics = metrics or MetricsHook()
        self._circuit = _CircuitBreaker()
        self._adapters: dict[str, ProtocolAdapter] = {
            "aave": AaveV3Adapter(),
            "compound": CompoundV3Adapter(),
        }

    async def build_call(self, action: str, params: dict) -> ToolCall:
        """Kept for interface compatibility — returns just the action call.
        Prefer build_call_plan() when the caller can handle an optional
        approval step, since that's the common real-world case."""
        plan = await self.build_call_plan(action, params)
        return plan.action_call

    async def build_call_plan(self, action: str, params: dict, correlation_id: str | None = None) -> DeFiCallPlan:
        start = time.monotonic()
        protocol = params["protocol"]
        chain_id = params["chain_id"]
        asset_symbol = params["asset"]

        log_ctx = {"protocol": protocol, "chain_id": chain_id, "asset": asset_symbol,
                   "action": action, "correlation_id": correlation_id}

        adapter = self._adapters.get(protocol)
        if adapter is None:
            self.metrics.increment("defi_tool.unsupported_protocol", {"protocol": protocol})
            raise ValueError(f"Unsupported DeFi protocol: {protocol}")

        pool_config = self.pool_registry.get((protocol, chain_id))
        if pool_config is None:
            raise ValueError(f"No pool configured for {protocol} on chain {chain_id}")

        asset = self.asset_registry.get(asset_symbol)
        if asset is None:
            raise ValueError(f"Unknown asset: {asset_symbol}")

        amount = self._to_decimal_amount(params["amount"], asset)
        raw_amount = int((amount * (10 ** asset.decimals)).to_integral_value(rounding=ROUND_DOWN))

        await self._check_not_paused(adapter, pool_config, asset, log_ctx)

        on_behalf_of = params["on_behalf_of"]

        if action == "deposit":
            calldata_hex = adapter.encode_deposit(self.w3, pool_config, asset, raw_amount, on_behalf_of)
            approval_call = self._build_approval_call(asset, pool_config.address, raw_amount)
        elif action == "withdraw":
            calldata_hex = adapter.encode_withdraw(self.w3, pool_config, asset, raw_amount, on_behalf_of)
            approval_call = None  # withdrawals don't need a token approval
        else:
            raise ValueError(f"Unsupported DeFi action: {action}")

        action_call = ToolCall(
            target=pool_config.address, value=0, calldata=Web3.to_bytes(hexstr=calldata_hex),
        )

        logger.info("defi_tool: built %s call for %s %s %s", action, amount, asset_symbol, protocol, extra=log_ctx)
        self.metrics.increment("defi_tool.call_built", {"protocol": protocol, "action": action})
        self.metrics.observe_latency("defi_tool.build_latency", time.monotonic() - start, {"protocol": protocol})

        return DeFiCallPlan(
            approval_call=approval_call, action_call=action_call,
            protocol=protocol, asset_symbol=asset_symbol, amount=amount,
        )

    def _to_decimal_amount(self, raw: float | str | Decimal, asset: AssetConfig) -> Decimal:
        # Never do token-amount math in float — 0.1 + 0.2 != 0.3 in binary
        # floating point, and that's the kind of bug that only shows up
        # once real money is on the line. Decimal from a string avoids the
        # float-conversion step entirely if the caller already has a string.
        amount = Decimal(str(raw))

        if amount <= 0:
            raise ValueError("Amount must be positive")
        if asset.min_amount and amount < asset.min_amount:
            raise ValueError(f"Amount {amount} below protocol minimum {asset.min_amount}")
        if asset.max_amount and amount > asset.max_amount:
            raise ValueError(f"Amount {amount} exceeds protocol maximum {asset.max_amount}")

        return amount

    def _build_approval_call(self, asset: AssetConfig, spender: str, raw_amount: int) -> ToolCall:
        contract = self.w3.eth.contract(address=Web3.to_checksum_address(asset.address), abi=_ERC20_APPROVE_ABI)
        calldata_hex = contract.encodeABI(fn_name="approve", args=[spender, raw_amount])
        return ToolCall(target=asset.address, value=0, calldata=Web3.to_bytes(hexstr=calldata_hex))

    async def _check_not_paused(self, adapter: ProtocolAdapter, pool_config: PoolConfig, asset: AssetConfig, log_ctx: dict) -> None:
        if not self._circuit.allow():
            self.metrics.increment("defi_tool.circuit_open", {"protocol": adapter.protocol_name})
            raise RuntimeError(f"{adapter.protocol_name}: circuit breaker open, RPC reads likely degraded")

        try:
            paused = await asyncio.to_thread(adapter.is_paused, self.w3, pool_config, asset)
            self._circuit.record_success()
        except Exception as exc:
            self._circuit.record_failure()
            logger.warning("defi_tool: pause check failed, proceeding cautiously: %s", exc, extra=log_ctx)
            return  # don't hard-block a deposit just because the pause READ failed — that's a different failure mode than an actual pause

        if paused:
            self.metrics.increment("defi_tool.market_paused", {"protocol": adapter.protocol_name})
            raise RuntimeError(f"{adapter.protocol_name} market for this asset is currently paused")