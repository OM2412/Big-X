import re
import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ChainId(str, Enum):
    ETHEREUM = "ethereum"
    BASE = "base"
    BITCOIN = "bitcoin"
    TRON = "tron"


@dataclass
class ChainConfig:
    chain_id: ChainId
    display_name: str
    rpc_url: str
    native_symbol: str
    native_decimals: int
    is_evm: bool
    explorer_url: str
    chain_id_numeric: int | None = None  # EVM chain ID, e.g. 8453 for Base — None for non-EVM chains


_EVM_ADDRESS_PATTERN = re.compile(r"^0x[a-fA-F0-9]{40}$")
_BITCOIN_ADDRESS_PATTERN = re.compile(r"^(bc1|[13])[a-zA-HJ-NP-Z0-9]{25,62}$")
_TRON_ADDRESS_PATTERN = re.compile(r"^T[a-zA-Z0-9]{33}$")


class MultichainSupportService:
    def __init__(self, chain_configs: dict[ChainId, ChainConfig]):
        self._configs = chain_configs

    def get_config(self, chain_id: ChainId) -> ChainConfig:
        config = self._configs.get(chain_id)
        if config is None:
            raise ValueError(f"Unsupported chain: {chain_id}")
        return config

    def is_valid_address(self, chain_id: ChainId, address: str) -> bool:
        if chain_id in (ChainId.ETHEREUM, ChainId.BASE):
            return bool(_EVM_ADDRESS_PATTERN.match(address))
        if chain_id == ChainId.BITCOIN:
            return bool(_BITCOIN_ADDRESS_PATTERN.match(address))
        if chain_id == ChainId.TRON:
            return bool(_TRON_ADDRESS_PATTERN.match(address))
        return False

    def normalize_address(self, chain_id: ChainId, address: str) -> str:
        """EVM addresses should be checksummed before use — TRON/Bitcoin
        addresses are case-sensitive as-is and pass through unchanged."""
        if chain_id in (ChainId.ETHEREUM, ChainId.BASE):
            from web3 import Web3
            return Web3.to_checksum_address(address)
        return address

    def list_supported_chains(self) -> list[ChainConfig]:
        return list(self._configs.values())


def default_chain_registry(env_rpc_urls: dict[str, str]) -> dict[ChainId, ChainConfig]:
    """Builds the standard 4-chain config your architecture diagram calls
    for (Bitcoin, Ethereum, Base, TRON). RPC URLs come from env/config —
    never hard-code a provider URL with an embedded API key in source."""
    return {
        ChainId.ETHEREUM: ChainConfig(
            chain_id=ChainId.ETHEREUM, display_name="Ethereum", rpc_url=env_rpc_urls["ETHEREUM_RPC_URL"],
            native_symbol="ETH", native_decimals=18, is_evm=True,
            explorer_url="https://etherscan.io", chain_id_numeric=1,
        ),
        ChainId.BASE: ChainConfig(
            chain_id=ChainId.BASE, display_name="Base", rpc_url=env_rpc_urls["BASE_RPC_URL"],
            native_symbol="ETH", native_decimals=18, is_evm=True,
            explorer_url="https://basescan.org", chain_id_numeric=8453,
        ),
        ChainId.BITCOIN: ChainConfig(
            chain_id=ChainId.BITCOIN, display_name="Bitcoin", rpc_url=env_rpc_urls["BTC_RPC_URL"],
            native_symbol="BTC", native_decimals=8, is_evm=False,
            explorer_url="https://mempool.space",
        ),
        ChainId.TRON: ChainConfig(
            chain_id=ChainId.TRON, display_name="TRON", rpc_url=env_rpc_urls["TRON_RPC_URL"],
            native_symbol="TRX", native_decimals=6, is_evm=False,
            explorer_url="https://tronscan.org",
        ),
    }