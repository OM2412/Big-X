
import uuid
from datetime import datetime

from sqlalchemy import Column, String, Float, Integer, Boolean, DateTime, Text, ForeignKey
from pgvector.sqlalchemy import Vector

from ..base import Base, TimestampMixin

EMBEDDING_DIM = 1536  # matches text-embedding-3-small — change if you swap embedding models


class AgentMemory(Base, TimestampMixin):
    __tablename__ = "agent_memories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    agent_id = Column(String(78), index=True)
    task_id = Column(String(36), index=True)

    summary = Column(Text)                      # human-readable lesson, e.g. "Aave deposits for USDC on Base consistently underestimate gas by ~15%"
    embedding = Column(Vector(EMBEDDING_DIM))     # semantic search over summary
    outcome = Column(String(20))                  # "success" | "failure" | "partial"
    tool = Column(String(50), nullable=True)
    protocol = Column(String(50), nullable=True)

    # What the pipeline predicted vs. what actually happened — the raw
    # material ReflectionAgent uses to write the summary above.
    predicted_gas = Column(Integer, nullable=True)
    actual_gas = Column(Integer, nullable=True)
    predicted_slippage_bps = Column(Integer, nullable=True)
    actual_slippage_bps = Column(Integer, nullable=True)


class ToolPerformanceStat(Base, TimestampMixin):
    __tablename__ = "tool_performance"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tool = Column(String(50), index=True)
    protocol = Column(String(50), index=True)      # e.g. "aave" vs "compound" under defi_tool, or a specific DEX under swap_tool
    chain_id = Column(Integer, index=True)

    total_attempts = Column(Integer, default=0)
    successful_attempts = Column(Integer, default=0)
    total_gas_estimate_error_pct = Column(Float, default=0.0)   # running sum, divide by attempts for average
    total_slippage_estimate_error_bps = Column(Float, default=0.0)
    avg_execution_latency_ms = Column(Float, default=0.0)

    @property
    def success_rate(self) -> float:
        if self.total_attempts == 0:
            return 0.0
        return self.successful_attempts / self.total_attempts