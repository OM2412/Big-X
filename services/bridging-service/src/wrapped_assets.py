import os
import json
import logging
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env.example", override=True)

logger = logging.getLogger(__name__)

RPC_URL = os.environ["BRIDGE_RPC_URL"]
WRAPPED_BTC_ADDRESS = os.environ["WRAPPED_BTC_ADDRESS"]

# Minimal ERC-20 ABI — only what this wrapper actually needs, rather than
# pulling in the full standard ABI for a handful of read/approve calls.
ERC20_ABI = json.loads("""[
    {"constant": true, "inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": true, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": true, "inputs": [], "name": "totalSupply", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": true, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": false, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"}
]""")


class WrappedAssetClient:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.contract = self.w3.eth.contract(address=WRAPPED_BTC_ADDRESS, abi=ERC20_ABI)
        self._decimals_cache: int | None = None

    def decimals(self) -> int:
        if self._decimals_cache is None:
            self._decimals_cache = self.contract.functions.decimals().call()
        return self._decimals_cache

    def balance_of(self, address: str) -> float:
        raw = self.contract.functions.balanceOf(address).call()
        return raw / (10 ** self.decimals())

    def total_supply(self) -> float:
        raw = self.contract.functions.totalSupply().call()
        return raw / (10 ** self.decimals())

    def allowance(self, owner: str, spender: str) -> float:
        raw = self.contract.functions.allowance(owner, spender).call()
        return raw / (10 ** self.decimals())

    def build_approve_tx(self, spender: str, amount: float, from_address: str) -> dict:
        """Returns an unsigned tx dict — signing happens wherever the
        caller's key actually lives (e.g. the agent's Token Bound Account
        via AgentAccount.execute, not here)."""
        raw_amount = int(amount * (10 ** self.decimals()))
        call = self.contract.functions.approve(spender, raw_amount)
        return call.build_transaction({
            "from": from_address,
            "nonce": self.w3.eth.get_transaction_count(from_address),
            "gas": 60_000,
            "gasPrice": self.w3.eth.gas_price,
        })