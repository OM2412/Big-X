
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from eth_account.messages import encode_defunct
from eth_account import Account
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from db.session import get_db_session

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_MINUTES = 60 * 24  # 24h

API_KEYS = set(os.environ.get("API_KEYS", "").split(","))  # comma-separated in env

bearer_scheme = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------------
# Data models
# --------------------------------------------------------------------------

class SessionUser(BaseModel):
    user_id: str
    wallet_address: Optional[str] = None
    role: str = "user"  # "user" | "admin" | "service"


class SiweLoginRequest(BaseModel):
    message: str      # the raw SIWE message the frontend had the wallet sign
    signature: str     # hex signature returned by the wallet


# --------------------------------------------------------------------------
# JWT session handling
# --------------------------------------------------------------------------

def create_session_token(user: SessionUser) -> str:
    """Issue a signed JWT for an authenticated user."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.user_id,
        "wallet": user.wallet_address,
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> SessionUser:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    return SessionUser(
        user_id=payload["sub"],
        wallet_address=payload.get("wallet"),
        role=payload.get("role", "user"),
    )


# --------------------------------------------------------------------------
# SIWE (Sign-in with Ethereum)
# --------------------------------------------------------------------------

def verify_siwe(login: SiweLoginRequest) -> str:
    """
    Verify a signed SIWE message and return the recovered wallet address.
    Raises 401 if the signature doesn't match the message.

    NOTE: for production, also parse the SIWE message fields (domain, nonce,
    expiration) with a proper SIWE parser and check the nonce against a
    server-side store to prevent replay attacks. This function only verifies
    the cryptographic signature.
    """
    try:
        encoded_message = encode_defunct(text=login.message)
        recovered_address = Account.recover_message(
            encoded_message, signature=login.signature
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid signature")

    return recovered_address


# --------------------------------------------------------------------------
# API key auth (service-to-service)
# --------------------------------------------------------------------------

def verify_api_key(request: Request) -> SessionUser:
    api_key = request.headers.get("X-API-Key")
    if not api_key or api_key not in API_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
    return SessionUser(user_id=f"service:{api_key[:8]}", role="service")


# --------------------------------------------------------------------------
# Combined auth dependency — accepts either a JWT bearer token or API key
# --------------------------------------------------------------------------

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    session=Depends(get_db_session),
) -> SessionUser:
    # Prefer bearer JWT if present
    if credentials is not None:
        user = decode_session_token(credentials.credentials)
        try:
            import uuid as uuid_module
            uuid_module.UUID(user.user_id)
        except (ValueError, AttributeError):
            from sqlalchemy import select
            from db.models.users import User
            stmt = select(User).where(User.wallet_address == user.user_id)
            result = await session.execute(stmt)
            db_user = result.scalar_one_or_none()
            if db_user:
                user = SessionUser(user_id=str(db_user.id), wallet_address=db_user.wallet_address, role=db_user.role)
            else:
                raise HTTPException(status_code=401, detail="Invalid user identifier")
        return user

    # Fall back to API key
    if request.headers.get("X-API-Key"):
        return verify_api_key(request)

    raise HTTPException(status_code=401, detail="Not authenticated")


# --------------------------------------------------------------------------
# RBAC — role-gated route dependency
# --------------------------------------------------------------------------

def require_role(*allowed_roles: str):
    """
    Usage:
        @app.get("/admin/agents")
        def list_agents(user: SessionUser = Depends(require_role("admin"))):
            ...
    """

    def dependency(user: SessionUser = Depends(get_current_user)) -> SessionUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(allowed_roles)}",
            )
        return user


