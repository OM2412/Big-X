from pydantic import BaseModel, Field, field_validator

from services.shared.src.blockchain_client import BaseContractClient, InputValidationError


class AgentRegistryConfig(BaseModel):
    operator_private_key: str
    network: str = "base"
    default_gas_limit: int = 400_000
    max_retries: int = 3


class TransactionReceipt(BaseModel):
    tx_hash: str
    gas_used: int
    block_number: int


class AgentRecord(BaseModel):
    owner: str
    capabilities: int
    model_version: str
    metadata_uri: str
    endpoint: str
    token_bound_account: str
    state: int


class AgentIdentity(BaseModel):
    name: str
    persona: str
    creator: str
    version: int
    created_at: int


def _validate_nft_id(nft_id: int) -> int:
    if nft_id < 0:
        raise InputValidationError(f"nft_id must be non-negative, got {nft_id}")
    return nft_id


def _validate_address(address: str) -> str:
    if not address.startswith("0x") or len(address) != 42:
        raise InputValidationError(f"Not a valid EVM address: {address}")
    return address


def _validate_capabilities(capabilities: int) -> int:
    if capabilities < 0:
        raise InputValidationError(f"capabilities bitmask must be non-negative, got {capabilities}")
    return capabilities


class AgentRegistryClient:
    def __init__(self, config: AgentRegistryConfig, rpc_provider_manager, **base_client_kwargs):
        self._client = BaseContractClient(
            contract_name="AgentRegistry", network=config.network,
            rpc_provider_manager=rpc_provider_manager,
            operator_private_key=config.operator_private_key,
            default_gas_limit=config.default_gas_limit, max_retries=config.max_retries,
            **base_client_kwargs,
        )

    def register_agent(
        self, nft_id: int, owner: str, name: str, persona: str,
        capabilities: int, model_version: str, metadata_uri: str, endpoint: str,
    ) -> TransactionReceipt:
        nft_id = _validate_nft_id(nft_id)
        owner = _validate_address(owner)
        capabilities = _validate_capabilities(capabilities)
        if not name.strip():
            raise InputValidationError("name cannot be empty")

        result = self._client.send("registerAgent", nft_id, owner, name, persona, capabilities, model_version, metadata_uri, endpoint)
        return TransactionReceipt(**result)

    def provision_account(self, nft_id: int, salt: bytes) -> TransactionReceipt:
        nft_id = _validate_nft_id(nft_id)
        if len(salt) != 32:
            raise InputValidationError(f"salt must be 32 bytes, got {len(salt)}")

        result = self._client.send("provisionAccount", nft_id, salt)
        return TransactionReceipt(**result)

    def activate(self, nft_id: int) -> TransactionReceipt:
        result = self._client.send("activate", _validate_nft_id(nft_id))
        return TransactionReceipt(**result)

    def suspend(self, nft_id: int) -> TransactionReceipt:
        result = self._client.send("suspend", _validate_nft_id(nft_id))
        return TransactionReceipt(**result)

    def grant_capability(self, nft_id: int, capability_bit: int) -> TransactionReceipt:
        nft_id = _validate_nft_id(nft_id)
        capability_bit = _validate_capabilities(capability_bit)
        result = self._client.send("grantCapability", nft_id, capability_bit)
        return TransactionReceipt(**result)

    def sync_owner(self, nft_id: int, new_owner: str) -> TransactionReceipt:
        nft_id = _validate_nft_id(nft_id)
        new_owner = _validate_address(new_owner)
        result = self._client.send("syncOwner", nft_id, new_owner)
        return TransactionReceipt(**result)

    def get_agent(self, nft_id: int) -> AgentRecord:
        result = self._client.call("getAgent", _validate_nft_id(nft_id))
        return AgentRecord(
            owner=result[0], capabilities=result[1], model_version=result[2],
            metadata_uri=result[3], endpoint=result[4], token_bound_account=result[5], state=result[6],
        )

    def get_identity(self, nft_id: int) -> AgentIdentity:
        result = self._client.call("getIdentity", _validate_nft_id(nft_id))
        return AgentIdentity(name=result[0], persona=result[1], creator=result[2], version=result[3], created_at=result[4])

    def has_capability(self, nft_id: int, capability_bit: int) -> bool:
        return self._client.call("hasCapability", _validate_nft_id(nft_id), _validate_capabilities(capability_bit))
