from fastapi import APIRouter

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])

@router.get("")
async def list_tasks():
    return {"tasks": []}
