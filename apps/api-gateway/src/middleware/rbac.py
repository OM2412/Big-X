from enum import Enum
from fastapi import Request, HTTPException, status

# ---------------- Roles ---------------- #
class Role(str, Enum):
    USER = "user"
    AGENT = "agent"
    ADMIN = "admin"

# ---------------- Permissions ---------------- #
ROLE_PERMISSIONS = {
    Role.USER: {"agent:read", "trade:view", "wallet:view", "policy:view"},
    Role.AGENT: {
        "agent:create", "agent:read", "agent:update", "agent:execute",
        "trade:execute", "trade:view",
        "wallet:view",
        "policy:view", "policy:update"
    },
    Role.ADMIN: {"*"}   # Full access
}

# ---------------- Endpoint Permissions ---------------- #
ENDPOINTS = {
    ("POST", "/api/agents"): "agent:create",
    ("GET", "/api/agents"): "agent:read",
    ("POST", "/api/trades"): "trade:execute",
    ("GET", "/api/trades"): "trade:view",
    ("GET", "/api/wallet"): "wallet:view",
    ("POST", "/api/wallet"): "wallet:manage",
    ("GET", "/api/policies"): "policy:view",
    ("PUT", "/api/policies"): "policy:update",
}

# ---------------- Helper ---------------- #
def get_role(req: Request):
    return Role(req.headers.get("X-User-Role", "user"))

def has_permission(role: Role, perm: str):
    return "*" in ROLE_PERMISSIONS[role] or perm in ROLE_PERMISSIONS[role]

# ---------------- Middleware ---------------- #
async def rbac_middleware(request: Request, call_next):

    if request.method == "OPTIONS":
        return await call_next(request)

    perm = ENDPOINTS.get((request.method, request.url.path))

    if perm:
        role = get_role(request)

        if not has_permission(role, perm):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission Denied"
            )

    return await call_next(request)