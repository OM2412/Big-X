
import os
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env.example", override=True)

logger = logging.getLogger(__name__)

RPC_URL = os.environ["BRIDGE_RPC_URL"]
BRIDGE_CONTRACT_ADDRESS = os.environ["BRIDGE_CONTRACT_ADDRESS"]
RELAYER_PRIVATE_KEY = os.environ["RELAYER_PRIVATE_KEY"]  # TODO: load from a KMS/secrets manager, not a raw env var, before mainnet

ABI_PATH = Path(__file__).parent / "abi" / "BridgeContract.json"


class BridgeClient:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(RELAYER_PRIVATE_KEY)

        with open(ABI_PATH) as f:
            abi = json.load(f)

        self.contract = self.w3.eth.contract(address=BRIDGE_CONTRACT_ADDRESS, abi=abi)

    def _send(self, function_call):
        """Signs and sends a contract transaction from the relayer account,
        waits for the receipt, and returns it."""
        tx = function_call.build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 300_000,
            "gasPrice": self.w3.eth.gas_price,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)

        if receipt.status != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")

        return receipt

    def confirm_peg_in(self, btc_tx_hash: bytes, recipient: str, amount: int):
        """Relayer attests a native BTC deposit was observed. Called by
        btc_custody.py once a deposit reaches sufficient confirmations."""
        logger.info("Confirming peg-in for btc_tx_hash=%s recipient=%s", btc_tx_hash.hex(), recipient)
        call = self.contract.functions.confirmPegIn(btc_tx_hash, recipient, amount)
        return self._send(call)

    def request_peg_out_status(self, peg_out_id: int) -> dict:
        result = self.contract.functions.pegOutRequests(peg_out_id).call()
        return {
            "requester": result[0],
            "amount": result[1],
            "btc_address": result[2],
            "confirmations": result[3],
            "requested_at": result[4],
            "executed": result[5],
        }

    def confirm_peg_out(self, peg_out_id: int):
        """Relayer confirms the native BTC side is ready to send. Requires
        the on-chain timelock to have elapsed — call request_peg_out_status
        first to check requested_at + PEG_OUT_TIMELOCK before calling this."""
        logger.info("Confirming peg-out id=%s", peg_out_id)
        call = self.contract.functions.confirmPegOut(peg_out_id)
        return self._send(call)

    def get_peg_in_status(self, btc_tx_hash: bytes) -> dict:
        result = self.contract.functions.pegInRequests(btc_tx_hash).call()
        return {
            "recipient": result[0],
            "amount": result[1],
            "confirmations": result[2],
            "executed": result[3],
        }