import logging
from dataclasses import dataclass
from enum import Enum

from bip_utils import (
    Bip39SeedGenerator,
    Bip39MnemonicGenerator,
    Bip39WordsNum,
    Bip44,
    Bip44Coins,
    Bip44Changes,
)

from .encryption import EncryptionService

logger = logging.getLogger(__name__)


class Chain(str, Enum):
    ETHEREUM = "ethereum"
    BASE = "base"       # same curve/derivation as Ethereum — shares BIP44 coin type
    BITCOIN = "bitcoin"
    TRON = "tron"


_BIP44_COIN_MAP = {
    Chain.ETHEREUM: Bip44Coins.ETHEREUM,
    Chain.BASE: Bip44Coins.ETHEREUM,  # Base is an EVM chain — same address derivation as Ethereum
    Chain.BITCOIN: Bip44Coins.BITCOIN,
    Chain.TRON: Bip44Coins.TRON,
}


@dataclass
class DerivedAddress:
    address: str
    chain: Chain
    derivation_path: str
    account_index: int
    address_index: int


class HdWalletService:
    def __init__(self, encryption_service: EncryptionService):
        self.encryption_service = encryption_service

    def generate_mnemonic(self, words: int = 24) -> str:
        """24 words (256-bit entropy) by default — stronger than the 12-word
        minimum, appropriate given this secures real fund custody."""
        word_count = Bip39WordsNum.WORDS_NUM_24 if words == 24 else Bip39WordsNum.WORDS_NUM_12
        return str(Bip39MnemonicGenerator().FromWordsNumber(word_count))

    def encrypt_mnemonic(self, mnemonic: str, associated_data: bytes) -> str:
        """associated_data should bind this ciphertext to something like the
        agent's nft_id, so a leaked ciphertext can't be silently reassigned
        to a different agent record even if the DEK is also compromised."""
        return self.encryption_service.encrypt_string(mnemonic, associated_data)

    def decrypt_mnemonic(self, encrypted_mnemonic: str, associated_data: bytes) -> str:
        return self.encryption_service.decrypt_string(encrypted_mnemonic, associated_data)

    def derive_address(
        self, mnemonic: str, chain: Chain, account_index: int = 0, address_index: int = 0, passphrase: str = "",
    ) -> DerivedAddress:
        """Derives a single address. Holds the decrypted mnemonic in memory
        only for the duration of this call — callers should decrypt just
        before calling this and let the mnemonic go out of scope immediately after."""
        seed_bytes = Bip39SeedGenerator(mnemonic).Generate(passphrase)
        coin_type = _BIP44_COIN_MAP[chain]

        bip44_ctx = Bip44.FromSeed(seed_bytes, coin_type)
        account_ctx = bip44_ctx.Purpose().Coin().Account(account_index)
        address_ctx = account_ctx.Change(Bip44Changes.CHAIN_EXT).AddressIndex(address_index)

        return DerivedAddress(
            address=address_ctx.PublicKey().ToAddress(),
            chain=chain,
            derivation_path=address_ctx.DerivationPath().ToStr(),
            account_index=account_index,
            address_index=address_index,
        )

    def derive_watch_only_addresses(
        self, xpub: str, chain: Chain, count: int, start_index: int = 0,
    ) -> list[DerivedAddress]:
        """Derives a batch of RECEIVE addresses from a public xpub only — no
        private key material touches this process. This is what
        btc_custody.py's deposit-address generation should actually call,
        rather than holding a mnemonic in the custody monitor at all."""
        coin_type = _BIP44_COIN_MAP[chain]
        account_ctx = Bip44.FromExtendedKey(xpub, coin_type)

        addresses = []
        for i in range(start_index, start_index + count):
            address_ctx = account_ctx.Change(Bip44Changes.CHAIN_EXT).AddressIndex(i)
            addresses.append(DerivedAddress(
                address=address_ctx.PublicKey().ToAddress(),
                chain=chain,
                derivation_path=address_ctx.DerivationPath().ToStr(),
                account_index=0,
                address_index=i,
            ))
        return addresses

    def get_watch_only_xpub(self, mnemonic: str, chain: Chain, account_index: int = 0, passphrase: str = "") -> str:
        """Exports the account-level extended PUBLIC key — safe to hand to a
        monitoring service (btc_custody.py) since it can derive addresses
        but never sign. Generate this once, store the xpub, and never load
        the mnemonic into that service's process again."""
        seed_bytes = Bip39SeedGenerator(mnemonic).Generate(passphrase)
        coin_type = _BIP44_COIN_MAP[chain]
        account_ctx = Bip44.FromSeed(seed_bytes, coin_type).Purpose().Coin().Account(account_index)
        return account_ctx.PublicKey().ToExtended()