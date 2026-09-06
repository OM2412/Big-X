import os
import json
import logging
from pathlib import Path

from web3 import Web3
from eth_account import Account

logger = logging.getLogger(__name__)

RPC_URL = os.environ["CHAIN_RPC_URL"]
AGENT_REGISTRY_ADDRESS = os.environ["AGENT_REGISTRY_ADDRESS"]
REGISTRAR_PRIVATE_KEY = os.environ["REGISTRAR_PRIVATE_KEY"]  # TODO: move to a secrets manager before mainnet

ABI_PATH = Path(__file__).parent / "abi" / "AgentRegistry.json"


class AgentRegistryClient:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(REGISTRAR_PRIVATE_KEY)

        with open(ABI_PATH) as f:
            abi = json.load(f)
        self.contract = self.w3.eth.contract(address=AGENT_REGISTRY_ADDRESS, abi=abi)

    def _send(self, function_call):
        tx = function_call.build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 400_000,
            "gasPrice": self.w3.eth.gas_price,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
        return receipt

    def register_agent(
        self, nft_id: int, owner: str, name: str, persona: str,
        capabilities: int, model_version: str, metadata_uri: str, endpoint: str,
    ):
        call = self.contract.functions.registerAgent(
            nft_id, owner, name, persona, capabilities, model_version, metadata_uri, endpoint
        )
        return self._send(call)

    def provision_account(self, nft_id: int, salt: bytes):
        call = self.contract.functions.provisionAccount(nft_id, salt)
        return self._send(call)

    def activate(self, nft_id: int):
        return self._send(self.contract.functions.activate(nft_id))

    def suspend(self, nft_id: int):
        return self._send(self.contract.functions.suspend(nft_id))

    def grant_capability(self, nft_id: int, capability_bit: int):
        return self._send(self.contract.functions.grantCapability(nft_id, capability_bit))

    def sync_owner(self, nft_id: int, new_owner: str):
        """Call this from your event listener whenever an ERC7857IntelligentNFT
        Transfer event fires, so the registry's owner field never drifts."""
        return self._send(self.contract.functions.syncOwner(nft_id, new_owner))

    def get_agent(self, nft_id: int) -> dict:
        result = self.contract.functions.getAgent(nft_id).call()
        return {
            "owner": result[0],
            "capabilities": result[1],
            "model_version": result[2],
            "metadata_uri": result[3],
            "endpoint": result[4],
            "token_bound_account": result[5],
            "state": result[6],  # LifecycleState enum index
        }

    def get_identity(self, nft_id: int) -> dict:
        result = self.contract.functions.getIdentity(nft_id).call()
        return {"name": result[0], "persona": result[1], "creator": result[2], "version": result[3], "created_at": result[4]}

    def has_capability(self, nft_id: int, capability_bit: int) -> bool:
        return self.contract.functions.hasCapability(nft_id, capability_bit).call()