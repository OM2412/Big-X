# db/models/orders_listings.py
#
# Marketplace state — off-chain index of Marketplace.sol listings, so your
# frontend can browse/filter agents for sale without RPC calls per listing.

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Numeric, DateTime, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class ListingStatus(str, enum.Enum):
    ACTIVE = "active"
    SOLD = "sold"
    CANCELLED = "cancelled"


class Listing(Base, TimestampMixin):
    __tablename__ = "orders_listings"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)

    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    buyer_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id"), nullable=True)

    price: Mapped[float] = mapped_column(Numeric(36, 18))
    status: Mapped[ListingStatus] = mapped_column(SAEnum(ListingStatus, name="listing_status"), default=ListingStatus.ACTIVE)

    protocol_fee_bps: Mapped[int] = mapped_column(default=250)  # snapshot of fee at listing time
    tx_hash: Mapped[Optional[str]] = mapped_column(String(66), nullable=True)  # the sale's on-chain tx, once sold

    listed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    sold_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship(back_populates="listings")
    seller: Mapped["User"] = relationship(foreign_keys=[seller_id])
    buyer: Mapped[Optional["User"]] = relationship(foreign_keys=[buyer_id])

    def __repr__(self) -> str:
        return f"<Listing agent_id={self.agent_id} price={self.price} status={self.status}>"