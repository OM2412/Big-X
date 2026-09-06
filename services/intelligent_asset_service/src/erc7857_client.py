import os
import json
import logging
from pathlib import Path

from web3 import Web3
from eth_account import Account

logger = logging.getLogger(__name__)

RPC_URL = os.environ["CHAIN_RPC_URL"]
NFT_CONTRACT_ADDRESS = os.environ["AGENT_NFT_ADDRESS"]
MINTER_PRIVATE_KEY = os.environ["MINTER_PRIVATE_KEY"]  # TODO: move to a secrets manager before mainnet

ABI_PATH = Path(__file__).parent / "abi" / "ERC7857IntelligentNFT.json"


class ERC7857Client:
    def __init__(self):
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL))
        self.account = Account.from_key(MINTER_PRIVATE_KEY)

        with open(ABI_PATH) as f:
            abi = json.load(f)
        self.contract = self.w3.eth.contract(address=NFT_CONTRACT_ADDRESS, abi=abi)

    def _send(self, function_call):
        tx = function_call.build_transaction({
            "from": self.account.address,
            "nonce": self.w3.eth.get_transaction_count(self.account.address),
            "gas": 350_000,
            "gasPrice": self.w3.eth.gas_price,
        })
        signed = self.account.sign_transaction(tx)
        tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt.status != 1:
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()}")
        return receipt

    def mint_agent(self, to: str, metadata_uri: str, encrypted_data_hash: bytes) -> tuple[int, dict]:
        """Mints a new agent NFT and returns (token_id, receipt). token_id is
        parsed from the AgentMinted event rather than assumed sequential,
        since a batch mint or reorg could otherwise throw off your indexing."""
        call = self.contract.functions.mintAgent(to, metadata_uri, encrypted_data_hash)
        receipt = self._send(call)

        minted_event = self.contract.events.AgentMinted().process_receipt(receipt)
        token_id = minted_event[0]["args"]["tokenId"]
        return token_id, receipt

    def get_agent_metadata(self, token_id: int) -> dict:
        result = self.contract.functions.getAgentMetadata(token_id).call()
        return {
            "encrypted_data_hash": result[0].hex(),
            "metadata_uri": result[1],
            "verifier": result[2],
        }

    def owner_of(self, token_id: int) -> str:
        return self.contract.functions.ownerOf(token_id).call()

    def set_verifier(self, token_id: int, verifier_address: str):
        return self._send(self.contract.functions.setVerifier(token_id, verifier_address))

    def transfer_with_proof(self, from_address: str, to_address: str, token_id: int, proof: bytes):
        call = self.contract.functions.transferWithProof(from_address, to_address, token_id, proof)
        return self._send(call)