import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, BigInteger, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class LifecycleState(str, enum.Enum):
    CREATED = "created"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"


class Agent(Base, TimestampMixin):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # On-chain identifiers â€” nft_id + chain_id together are the real primary key
    # on the contract side; kept unique here too so syncing never double-inserts.
    nft_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    chain_id: Mapped[int] = mapped_column(Integer, default=8453)  # e.g. Base mainnet

    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    creator_wallet: Mapped[str] = mapped_column(String(42))

    name: Mapped[str] = mapped_column(String(100))
    persona: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    model_version: Mapped[str] = mapped_column(String(50))
    metadata_uri: Mapped[str] = mapped_column(String(500))  # IPFS/Arweave pointer, matches AgentMetadata.metadataURI
    endpoint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # off-chain API the agent runs on

    token_bound_account: Mapped[Optional[str]] = mapped_column(String(42), nullable=True)  # ERC-6551 wallet address
    capabilities: Mapped[int] = mapped_column(BigInteger, default=0)  # bitmask, matches CapabilityRegistry bits

    state: Mapped[LifecycleState] = mapped_column(SAEnum(LifecycleState, name="lifecycle_state"), default=LifecycleState.CREATED)

    last_synced_block: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    owner: Mapped["User"] = relationship(back_populates="agents")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="agent")
    execution_steps: Mapped[list["ExecutionStep"]] = relationship(back_populates="agent")
    listings: Mapped[list["Listing"]] = relationship(back_populates="agent")
    nft_metadata: Mapped[Optional["NFTMetadata"]] = relationship(back_populates="agent", uselist=False)

    def has_capability(self, bit: int) -> bool:
        return bool(self.capabilities & bit)

    def __repr__(self) -> str:
        return f"<Agent nft_id={self.nft_id} name={self.name} state={self.state}>"