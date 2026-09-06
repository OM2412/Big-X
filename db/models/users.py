# db/models/users.py
#
# Layer 1 (User & Access) + Layer A (Auth & Identity) —
# a user is identified by wallet address, authenticated via SIWE.

import uuid
from typing import Optional

from sqlalchemy import String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    wallet_address: Mapped[str] = mapped_column(String(42), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # "user" | "admin" | "service"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # SIWE nonce tracking — set on login challenge, cleared after use, to
    # prevent replay of a previously signed message (see auth.py's TODO).
    siwe_nonce: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    sessions: Mapped[list["Session"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    agents: Mapped[list["Agent"]] = relationship(back_populates="owner")

    def __repr__(self) -> str:
        return f"<User {self.wallet_address}>"