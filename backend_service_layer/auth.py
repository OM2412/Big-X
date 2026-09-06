import hashlib
import logging
import secrets
from pydantic import BaseModel
from fastapi import Depends, HTTPException
import jwt

JWT_SECRET = "dev-secret"
JWT_ALGORITHM = "HS256"
JWT_EXPIRY = 60 * 60 * 24

class SessionUser(BaseModel):
    user_id: str
    wallet_address: str | None = None
    role: str = "user"

class SiweLoginRequest(BaseModel):
    message: str
    signature: str

class NonceResponse(BaseModel):
    nonce: str

_nonces: dict[str, str] = {}

def generate_nonce() -> str:
    nonce = secrets.token_urlsafe(16)
    return nonce

def store_nonce(address: str, nonce: str) -> None:
    _nonces[address.lower()] = nonce

def verify_nonce(address: str, nonce: str) -> bool:
    stored = _nonces.get(address.lower())
    if not stored:
        return False
    if stored != nonce:
        return False
    del _nonces[address.lower()]
    return True

def create_session_token(user: SessionUser) -> str:
    import time
    payload = {"sub": user.user_id, "wallet": user.wallet_address, "role": user.role, "exp": time.time() + JWT_EXPIRY}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

def verify_siwe(login: SiweLoginRequest) -> tuple[str, str]:
    from eth_account.messages import encode_defunct
    from eth_account import Account
    try:
        encoded_message = encode_defunct(text=login.message)
        recovered_address = Account.recover_message(encoded_message, signature=login.signature)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    nonce = ""
    for line in login.message.split("\n"):
        if line.startswith("Nonce:"):
            nonce = line.split(":", 1)[1].strip()
            break
    
    if not nonce:
        raise HTTPException(status_code=400, detail="Missing nonce in SIWE message")
    
    if not verify_nonce(recovered_address, nonce):
        raise HTTPException(status_code=401, detail="Invalid or expired nonce")
    
    return recovered_address, nonce

def get_current_user(request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = auth.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    return SessionUser(user_id=payload["sub"], wallet_address=payload.get("wallet"), role=payload.get("role", "user"))
