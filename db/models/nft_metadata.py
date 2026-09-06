
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, ForeignKey, DateTime, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class VerifierType(str, enum.Enum):
    NONE = "none"
    TEE = "tee"
    ZKP = "zkp"


class NFTMetadata(Base, TimestampMixin):
    __tablename__ = "nft_metadata"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), unique=True, index=True)

    encrypted_data_hash: Mapped[str] = mapped_column(String(66))  # bytes32 hash, hex-encoded
    token_uri: Mapped[str] = mapped_column(String(500))
    verifier_contract: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)

    verifier_type: Mapped[VerifierType] = mapped_column(SAEnum(VerifierType, name="verifier_type"), default=VerifierType.NONE)
    last_attestation_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)
    last_attested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    royalty_receiver: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)
    royalty_bps: Mapped[int] = mapped_column(Integer, default=0)

    agent: Mapped["Agent"] = relationship(back_populates="nft_metadata")

    def __repr__(self) -> str:
        return f"<NFTMetadata agent_id={self.agent_id} verifier={self.verifier_type}>"