# apps/api-gateway/src/routes/agents.py
"""Agent management routes, gated by RBAC roles."""

from fastapi import APIRouter, Depends

from ..middleware.auth import SessionUser, require_role

router = APIRouter(prefix="/admin", tags=["agents"])


@router.get("/agents")
def admin_only(user: SessionUser = Depends(require_role("admin"))):
    return {"ok": True}
