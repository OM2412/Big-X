from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_db_session

DbSession = AsyncSession

async def current_user(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth.split(" ", 1)[1]
    from backend_service_layer.auth import SessionUser, get_current_user
    return get_current_user(request)

def db_session():
    return get_db_session()
