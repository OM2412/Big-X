import logging
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from bridge_client import BridgeClient

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env.example", override=True)

logger = logging.getLogger(__name__)


@dataclass
class RedemptionResult:
    peg_out_id: int
    btc_txid: str | None
    status: str  # "sent" | "failed" | "waiting_for_timelock"


class PegOutRedemptionService:
    def __init__(self, bridge_client: BridgeClient, db_session_factory):
        self.bridge_client = bridge_client
        self.db_session_factory = db_session_factory

    async def process_pending_redemptions(self, pending_peg_out_ids: list[int]) -> list[RedemptionResult]:
        results = []
        for peg_out_id in pending_peg_out_ids:
            result = await self._process_one(peg_out_id)
            results.append(result)
        return results

    async def _process_one(self, peg_out_id: int) -> RedemptionResult:
        status = self.bridge_client.request_peg_out_status(peg_out_id)

        if status["executed"]:
            logger.info("Peg-out %s already executed on-chain, sending native BTC now", peg_out_id)
            return await self._finalize(peg_out_id, status)

        # Not yet confirmed by enough relayers / timelock hasn't elapsed —
        # confirm_peg_out will simply revert if called too early, so check first.
        try:
            self.bridge_client.confirm_peg_out(peg_out_id)
            status = self.bridge_client.request_peg_out_status(peg_out_id)
            if status["executed"]:
                return await self._finalize(peg_out_id, status)
        except Exception:
            logger.info("Peg-out %s not ready yet (timelock or confirmations)", peg_out_id)

        return RedemptionResult(peg_out_id=peg_out_id, btc_txid=None, status="waiting_for_timelock")

    async def _finalize(self, peg_out_id: int, status: dict) -> RedemptionResult:
        btc_address = status["btc_address"]
        amount = status["amount"]

        try:
            btc_txid = await self._send_native_btc(btc_address, amount)
            await self._record_redemption(peg_out_id, btc_txid, "sent")
            return RedemptionResult(peg_out_id=peg_out_id, btc_txid=btc_txid, status="sent")
        except Exception:
            logger.exception("Failed to send native BTC for peg-out %s", peg_out_id)
            await self._record_redemption(peg_out_id, None, "failed")
            return RedemptionResult(peg_out_id=peg_out_id, btc_txid=None, status="failed")

    async def _send_native_btc(self, btc_address: str, amount: int) -> str:
        # TODO: this must call out to your actual BTC custody signer
        # (multisig coordinator, HSM, or custody provider API) — never a
        # private key held directly in this service.
        raise NotImplementedError("Wire this up to your BTC custody signer, not a local key")

    async def _record_redemption(self, peg_out_id: int, btc_txid: str | None, status: str):
        async with self.db_session_factory() as session:
            # TODO: persist to a redemptions table for audit/reconciliation.
            pass