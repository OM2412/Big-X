

import enum
import uuid
from typing import Optional

from sqlalchemy import String, Integer, Text, ForeignKey, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class AgentRole(str, enum.Enum):
    PLANNER = "planner"
    MEMORY = "memory"
    SIMULATOR = "simulator"
    EXECUTOR = "executor"
    CRITIC = "critic"


class StepStatus(str, enum.Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRIED = "retried"


class ExecutionStep(Base, TimestampMixin):
    __tablename__ = "execution_history"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # Groups every step belonging to one user request together, e.g.
    # "Buy $100 of BTC and yield farm it on Aave" -> one task_id, five steps.
    task_id: Mapped[uuid.UUID] = mapped_column(index=True)

    agent_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agents.id"), index=True)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        ForeignKey("transactions.id"), nullable=True
    )

    role: Mapped[AgentRole] = mapped_column(SAEnum(AgentRole, name="agent_role"))
    status: Mapped[StepStatus] = mapped_column(SAEnum(StepStatus, name="step_status"), default=StepStatus.STARTED)
    sequence: Mapped[int] = mapped_column(Integer)  # order within the task, e.g. 1=planner, 2=simulator...
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    input_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Simulator-specific fields — estimated before execution, compared to
    # actuals on the linked Transaction once it confirms.
    estimated_gas: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estimated_slippage_bps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    risk_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # 0-100, from risk & policy engine

    agent: Mapped["Agent"] = relationship(back_populates="execution_steps")
    transaction: Mapped[Optional["Transaction"]] = relationship(back_populates="execution_step")

    def __repr__(self) -> str:
        return f"<ExecutionStep task={self.task_id} role={self.role} status={self.status}>"