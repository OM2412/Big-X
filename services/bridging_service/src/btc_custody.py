import os
import time
import logging
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from bitcoinrpc.authproxy import AuthServiceProxy

from bridge_client import BridgeClient

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env.example", override=True)

logger = logging.getLogger(__name__)

BTC_RPC_URL = os.environ["BTC_RPC_URL"]  # e.g. http://user:pass@localhost:8332
REQUIRED_CONFIRMATIONS = int(os.environ.get("BTC_REQUIRED_CONFIRMATIONS", "6"))
POLL_INTERVAL_SECONDS = 30


@dataclass
class DepositAddress:
    address: str
    recipient_evm_address: str  # the EVM address that should receive wrapped BTC
    derivation_index: int


class BtcCustodyMonitor:
    def __init__(self, bridge_client: BridgeClient, watch_only_xpub: str):
        self.bridge_client = bridge_client
        self.watch_only_xpub = watch_only_xpub  # public key only — cannot sign, safe to hold here
        self.btc_rpc = AuthServiceProxy(BTC_RPC_URL)
        self._seen_txids: set[str] = set()

    def derive_deposit_address(self, recipient_evm_address: str, derivation_index: int) -> DepositAddress:
        """Derives a fresh watch-only deposit address for a user from the xpub.
        TODO: wire up real BIP32 derivation (e.g. via `bitcoinlib` or `bip32utils`)
        against self.watch_only_xpub at m/0/{derivation_index}."""
        raise NotImplementedError("Wire up BIP32 derivation against watch_only_xpub")

    def poll_for_deposits(self, tracked_addresses: dict[str, str]):
        """tracked_addresses: {btc_address: recipient_evm_address}.
        Runs forever — deploy as a long-lived worker process, not inside a request handler."""
        logger.info("Starting BTC deposit monitor for %d addresses", len(tracked_addresses))

        while True:
            for btc_address, recipient in tracked_addresses.items():
                self._check_address(btc_address, recipient)
            time.sleep(POLL_INTERVAL_SECONDS)

    def _check_address(self, btc_address: str, recipient_evm_address: str):
        received = self.btc_rpc.listreceivedbyaddress(REQUIRED_CONFIRMATIONS, False, True, btc_address)

        for entry in received:
            for txid in entry.get("txids", []):
                if txid in self._seen_txids:
                    continue

                confirmations = self.btc_rpc.gettransaction(txid).get("confirmations", 0)
                if confirmations < REQUIRED_CONFIRMATIONS:
                    continue

                amount_btc = entry["amount"]
                logger.info("Confirmed deposit: txid=%s amount=%s to %s", txid, amount_btc, btc_address)

                self._attest_peg_in(txid, recipient_evm_address, amount_btc)
                self._seen_txids.add(txid)

    def _attest_peg_in(self, txid: str, recipient_evm_address: str, amount_btc: float):
        btc_tx_hash = bytes.fromhex(txid)
        amount_wei = int(amount_btc * 10**18)  # match your wrapped token's decimals

        try:
            self.bridge_client.confirm_peg_in(btc_tx_hash, recipient_evm_address, amount_wei)
        except Exception:
            logger.exception("Failed to attest peg-in for txid=%s — will retry next poll", txid)