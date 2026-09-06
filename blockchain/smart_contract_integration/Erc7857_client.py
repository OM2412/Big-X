import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel

from Smart_contract_integration.blockchain_client import BaseContractClient, InputValidationError, validate_address


class ERC7857Config(BaseModel):
    operator_private_key: str  # MINTER_ROLE key
    network: str = "base"
    default_gas_limit: int = 350_000
    max_retries: int = 3

class TransactionReceipt(BaseModel):
    tx_hash: str
    gas_used: int
    block_number: int

class AgentMetadata(BaseModel):
    encrypted_data_hash: str
    metadata_uri: str
    verifier: str


def _validate_token_id(token_id: int) -> int:
    if token_id < 0:
        raise InputValidationError(f"token_id must be non-negative, got {token_id}")
    return token_id


class ERC7857Client:
    def __init__(self, config: ERC7857Config, rpc_provider_manager, **base_client_kwargs):
        self._client = BaseContractClient(
            contract_name="ERC7857IntelligentNFT", network=config.network,
            rpc_provider_manager=rpc_provider_manager,
            operator_private_key=config.operator_private_key,
            default_gas_limit=config.default_gas_limit, max_retries=config.max_retries,
            **base_client_kwargs,
        )

    async def mint_agent(self, to: str, metadata_uri: str, encrypted_data_hash: bytes, correlation_id: str | None = None) -> tuple[int, TransactionReceipt]:
        to = validate_address(to)
        if len(encrypted_data_hash) != 32:
            raise InputValidationError(f"encrypted_data_hash must be 32 bytes, got {len(encrypted_data_hash)}")

        result = await self._client.asend("mintAgent", to, metadata_uri, encrypted_data_hash, correlation_id=correlation_id)

        # Fixed: previously indexed minted_events[0] directly, which would
        # raise an unhelpful IndexError if the event was ever missing.
        # get_event_args raises a specific EventNotFoundError instead.
        event_args = await asyncio.to_thread(self._client.get_event_args, "AgentMinted", result["tx_hash"])
        token_id = event_args["tokenId"]

        return token_id, TransactionReceipt(**result)

    def batch_mint_agents(self, recipients: list[str], metadata_uris: list[str], encrypted_data_hashes: list[bytes]) -> TransactionReceipt:
        if not (len(recipients) == len(metadata_uris) == len(encrypted_data_hashes)):
            raise InputValidationError("recipients, metadata_uris, and encrypted_data_hashes must be the same length")
        recipients = [validate_address(r) for r in recipients]

        result = self._client.send("batchMintAgents", recipients, metadata_uris, encrypted_data_hashes)
        return TransactionReceipt(**result)

    def get_agent_metadata(self, token_id: int) -> AgentMetadata:
        result = self._client.call("getAgentMetadata", _validate_token_id(token_id))
        return AgentMetadata(encrypted_data_hash=result[0].hex(), metadata_uri=result[1], verifier=result[2])

    def owner_of(self, token_id: int) -> str:
        return self._client.call("ownerOf", _validate_token_id(token_id))

    def set_verifier(self, token_id: int, verifier_address: str) -> TransactionReceipt:
        result = self._client.send("setVerifier", _validate_token_id(token_id), validate_address(verifier_address))
        return TransactionReceipt(**result)

    def transfer_with_proof(self, from_address: str, to_address: str, token_id: int, proof: bytes) -> TransactionReceipt:
        result = self._client.send(
            "transferWithProof", validate_address(from_address), validate_address(to_address),
            _validate_token_id(token_id), proof,
        )
        return TransactionReceipt(**result)