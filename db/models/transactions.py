
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Numeric, Integer, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class TransactionType(str, enum.Enum):
    SWAP = "swap"
    BRIDGE = "bridge"
    YIELD_DEPOSIT = "yield_deposit"
    YIELD_WITHDRAW = "yield_withdraw"
    NFT_TRADE = "nft_trade"
    LENDING = "lending"


class TransactionStatus(str, enum.Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    REVERTED = "reverted"


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)

    tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True, index=True)
    chain_id: Mapped[int] = mapped_column(Integer)

    tx_type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType, name="transaction_type"))
    status: Mapped[TransactionStatus] = mapped_column(SAEnum(TransactionStatus, name="transaction_status"), default=TransactionStatus.PENDING)

    from_address: Mapped[str] = mapped_column(String(42))
    to_address: Mapped[str] = mapped_column(String(42))
    token_symbol: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Numeric(36, 18), nullable=True)
    amount_usd: Mapped[Optional[float]] = mapped_column(Numeric(18, 2), nullable=True)

    gas_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gas_price_gwei: Mapped[Optional[float]] = mapped_column(Numeric(18, 9), nullable=True)

    # Links back to the PolicyEngine check that approved this before submission —
    # useful for audit trail ("why was this allowed to execute").
    policy_check_passed: Mapped[Optional[bool]] = mapped_column(nullable=True)

    submitted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="transactions")
    execution_step: Mapped[Optional["ExecutionStep"]] = relationship(back_populates="transaction", uselist=False)

    def __repr__(self) -> str:
        return f"<Transaction {self.tx_type} status={self.status} hash={self.tx_hash}>"