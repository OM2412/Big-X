from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..middleware.auth import (
    SiweLoginRequest,
    SessionUser,
    create_session_token,
    get_current_user,
    verify_siwe,
)
from db.session import get_db_session
from db.models.users import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/siwe")
async def login(login: SiweLoginRequest, session=Depends(get_db_session)):
    wallet_address = verify_siwe(login)
    wallet = wallet_address.lower()

    result = await session.execute(select(User).where(User.wallet_address == wallet))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(wallet_address=wallet, role="user")
        session.add(user)
        await session.commit()
        await session.refresh(user)

    session_user = SessionUser(user_id=str(user.id), wallet_address=wallet, role=user.role)
    token = create_session_token(session_user)
    return {"access_token": token, "token_type": "bearer"}


@router.get("/me")
def me(user: SessionUser = Depends(get_current_user)):
    return user
