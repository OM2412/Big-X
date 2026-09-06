
import logging
from dataclasses import dataclass

from web3 import Web3

logger = logging.getLogger(__name__)


@dataclass
class GasEstimate:
    max_fee_per_gas: int
    max_priority_fee_per_gas: int
    estimated_cost_wei: int
    estimated_cost_usd: float | None = None


class GasOptimizer:
    def __init__(self, w3: Web3):
        self.w3 = w3

    def estimate(self, gas_limit: int, urgency: str = "standard", native_price_usd: float | None = None) -> GasEstimate:
        """urgency: "slow" | "standard" | "fast" — controls the priority fee
        percentile pulled from recent fee history."""
        fee_history = self.w3.eth.fee_history(20, "latest", [self._percentile_for(urgency)])

        base_fees = fee_history["baseFeePerGas"]
        next_base_fee = base_fees[-1]  # fee_history includes one extra "next block" estimate

        priority_fees = [r[0] for r in fee_history["reward"] if r]
        priority_fee = self._median(priority_fees) if priority_fees else self.w3.to_wei(1.5, "gwei")

        # Add headroom on the base fee so the tx doesn't get stuck if the
        # next block's base fee rises — standard practice, not being sloppy.
        max_fee_per_gas = next_base_fee * 2 + priority_fee

        estimated_cost_wei = max_fee_per_gas * gas_limit
        estimated_cost_usd = None
        if native_price_usd is not None:
            estimated_cost_usd = (estimated_cost_wei / 1e18) * native_price_usd

        return GasEstimate(
            max_fee_per_gas=max_fee_per_gas,
            max_priority_fee_per_gas=priority_fee,
            estimated_cost_wei=estimated_cost_wei,
            estimated_cost_usd=estimated_cost_usd,
        )

    def _percentile_for(self, urgency: str) -> int:
        return {"slow": 10, "standard": 50, "fast": 90}.get(urgency, 50)

    def _median(self, values: list[int]) -> int:
        sorted_values = sorted(values)
        mid = len(sorted_values) // 2
        if len(sorted_values) % 2 == 0:
            return (sorted_values[mid - 1] + sorted_values[mid]) // 2
        return sorted_values[mid]