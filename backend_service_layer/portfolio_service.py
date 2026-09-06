import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.agents import Agent


@dataclass
class Position:
    agent_name: str
    asset: str
    amount: float
    value_usd: float


@dataclass
class Portfolio:
    total_value_usd: float
    positions: list[Position]


class PortfolioService:
    def __init__(self, wallet_tool, price_feed_tool):
        self.wallet_tool = wallet_tool
        self.price_feed_tool = price_feed_tool

    async def get_for_user(self, session: AsyncSession, user_id: str) -> Portfolio:
        stmt = select(Agent).where(Agent.owner_id == uuid.UUID(user_id), Agent.token_bound_account.isnot(None))
        result = await session.execute(stmt)
        agents = result.scalars().all()

        if not agents:
            return Portfolio(total_value_usd=0.0, positions=[])

        positions: list[Position] = []
        total_value_usd = 0.0

        for agent in agents:
            native_balance_wei = self.wallet_tool.get_native_balance(agent.token_bound_account)
            if native_balance_wei == 0:
                continue

            native_amount = native_balance_wei / 1e18
            try:
                price_result = await self.price_feed_tool.query("ETH")
                price = price_result.get("median_price", 0)
            except Exception:
                price = 0
            value_usd = native_amount * price

            positions.append(Position(agent_name=agent.name, asset="ETH", amount=native_amount, value_usd=value_usd))
            total_value_usd += value_usd

        return Portfolio(total_value_usd=total_value_usd, positions=positions)