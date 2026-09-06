from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from ..dependencies import current_user, db_session, DbSession
from ..auth import SessionUser
from ..services.portfolio_service import PortfolioService

router = APIRouter(prefix="/v1/portfolio", tags=["portfolio"])


class PositionResponse(BaseModel):
    agent_name: str
    asset: str
    amount: float
    value_usd: float


class PortfolioResponse(BaseModel):
    total_value_usd: float
    positions: list[PositionResponse]


def get_portfolio_service(request: Request) -> PortfolioService:
    return request.app.state.portfolio_service


@router.get("", response_model=PortfolioResponse)
async def get_portfolio(
    user: SessionUser = Depends(current_user), session: DbSession = Depends(db_session),
    service: PortfolioService = Depends(get_portfolio_service),
):
    portfolio = await service.get_for_user(session, user.user_id)
    return PortfolioResponse(
        total_value_usd=portfolio.total_value_usd,
        positions=[PositionResponse(agent_name=p.agent_name, asset=p.asset, amount=p.amount, value_usd=p.value_usd) for p in portfolio.positions],
    )
