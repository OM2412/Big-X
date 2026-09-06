import os
import base64
import logging
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

logger = logging.getLogger(__name__)

NONCE_SIZE_BYTES = 12   # standard for AES-GCM
KEY_SIZE_BYTES = 32      # AES-256


class DecryptionError(Exception):
    """Raised on auth-tag mismatch — either corrupted ciphertext or a
    tampering attempt. Never silently returns garbage plaintext."""


@dataclass
class EncryptedPayload:
    ciphertext: bytes
    nonce: bytes
    key_version: int  # which KMS-wrapped key encrypted this — required for rotation

    def to_storage_string(self) -> str:
        """Single string safe to store in a DB column."""
        return f"{self.key_version}:{base64.b64encode(self.nonce).decode()}:{base64.b64encode(self.ciphertext).decode()}"

    @classmethod
    def from_storage_string(cls, raw: str) -> "EncryptedPayload":
        version_str, nonce_b64, ciphertext_b64 = raw.split(":", 2)
        return cls(
            ciphertext=base64.b64decode(ciphertext_b64),
            nonce=base64.b64decode(nonce_b64),
            key_version=int(version_str),
        )


class KmsKeyProvider:
    """Abstraction over your KMS provider (AWS KMS, GCP KMS, HashiCorp
    Vault). Each `key_version` corresponds to a distinct master key —
    incrementing lets you rotate without touching already-encrypted data
    until it's next read/re-encrypted.

    This default implementation reads a key from an env var for local dev
    ONLY. Swap this for a real KMS client before anything but local testing —
    a key sitting in an env var defeats the point of envelope encryption."""

    def __init__(self, current_version: int = 1):
        self.current_version = current_version

    def get_data_encryption_key(self, key_version: int) -> bytes:
        # TODO: replace with a real KMS decrypt/unwrap call, e.g.:
        #   kms_client.decrypt(CiphertextBlob=wrapped_key_for_version[key_version])
        raw = os.environ.get(f"DEK_V{key_version}")
        if raw is None:
            raise ValueError(f"No data encryption key configured for version {key_version}")
        key = base64.b64decode(raw)
        if len(key) != KEY_SIZE_BYTES:
            raise ValueError(f"Data encryption key v{key_version} is not {KEY_SIZE_BYTES} bytes")
        return key


class EncryptionService:
    def __init__(self, key_provider: KmsKeyProvider):
        self.key_provider = key_provider

    def encrypt(self, plaintext: bytes, associated_data: bytes | None = None) -> EncryptedPayload:
        key_version = self.key_provider.current_version
        key = self.key_provider.get_data_encryption_key(key_version)

        nonce = os.urandom(NONCE_SIZE_BYTES)
        aesgcm = AESGCM(key)
        ciphertext = aesgcm.encrypt(nonce, plaintext, associated_data)

        return EncryptedPayload(ciphertext=ciphertext, nonce=nonce, key_version=key_version)

    def decrypt(self, payload: EncryptedPayload, associated_data: bytes | None = None) -> bytes:
        key = self.key_provider.get_data_encryption_key(payload.key_version)
        aesgcm = AESGCM(key)

        try:
            return aesgcm.decrypt(payload.nonce, payload.ciphertext, associated_data)
        except InvalidTag as exc:
            logger.error("Decryption failed — ciphertext tampered or wrong key version")
            raise DecryptionError("Authentication tag mismatch") from exc

    def encrypt_string(self, plaintext: str, associated_data: bytes | None = None) -> str:
        payload = self.encrypt(plaintext.encode("utf-8"), associated_data)
        return payload.to_storage_string()

    def decrypt_string(self, storage_string: str, associated_data: bytes | None = None) -> str:
        payload = EncryptedPayload.from_storage_string(storage_string)
        return self.decrypt(payload, associated_data).decode("utf-8")