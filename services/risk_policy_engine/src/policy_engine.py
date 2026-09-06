 
import os
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env.example", override=True)

logger = logging.getLogger(__name__)

RPC_URL = os.environ["CHAIN_RPC_URL"]
POLICY_ENGINE_ADDRESS = os.environ["POLICY_ENGINE_ADDRESS"]
POLICY_ADMIN_PRIVATE_KEY = os.environ["POLICY_ADMIN_PRIVATE_KEY"]
 
ABI_PATH = Path(__file__).parent / "abi" / "PolicyEngine.json"
 
 
class PolicyEngineClient:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(POLICY_ADMIN_PRIVATE_KEY)
 
        with open(ABI_PATH) as f:
            abi = json.load(f)
        self.contract = self.w3.eth.contract(address=POLICY_ENGINE_ADDRESS, abi=abi)
 
    def _send(self, function_call):
        tx = function_call.build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 200_000,
            "gasPrice": self.w3.eth.gas_price,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
        return receipt
 
    async def check_action(self, agent_id: str, target: str, value: int, data: bytes) -> tuple[bool, str]:
        """Read-only — no gas cost, safe to call from simulator.py before
        every single execution attempt."""
        allowed, reason = self.contract.functions.checkAction(
            int(agent_id), Web3.to_checksum_address(target), value, data
        ).call()
        return allowed, reason
 
    async def record_spend(self, agent_id: str, value: int):
        """Called by executor.py AFTER a transaction confirms successfully —
        debits the on-chain daily budget so the next checkAction sees it."""
        call = self.contract.functions.recordSpend(int(agent_id), value)
        return self._send(call)
 
    def set_policy(self, agent_id: int, per_tx_limit: int, daily_limit: int, human_approval_threshold: int):
        call = self.contract.functions.setPolicy(agent_id, per_tx_limit, daily_limit, human_approval_threshold)
        return self._send(call)
 
    def set_target_allowed(self, agent_id: int, target: str, allowed: bool):
        call = self.contract.functions.setTargetAllowed(agent_id, Web3.to_checksum_address(target), allowed)
        return self._send(call)
 
    def approve_action(self, agent_id: int, action_hash: bytes):
        """Called from your dashboard when a human approves a high-value
        action that tripped the humanApprovalThreshold."""
        call = self.contract.functions.approveAction(agent_id, action_hash)
        return self._send(call)