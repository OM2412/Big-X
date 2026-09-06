from pydantic import BaseModel, Field, field_validator

KNOWN_TOOLS = {
    "swap_tool", "bridge_tool", "defi_tool", "nft_tool", "wallet_tool", "price_feed_tool",
}


class SubTaskModel(BaseModel):
    tool: str
    action: str
    params: dict = Field(default_factory=dict)

    @field_validator("tool")
    @classmethod
    def tool_must_be_known(cls, v: str) -> str:
        if v not in KNOWN_TOOLS:
            raise ValueError(f"Unknown tool: {v}")
        return v

    @field_validator("action")
    @classmethod
    def action_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("action cannot be empty")
        return v


class PlannerOutputModel(BaseModel):
    subtasks: list[SubTaskModel]

    @field_validator("subtasks")
    @classmethod
    def must_have_at_least_one(cls, v: list) -> list:
        if not v:
            raise ValueError("Planner must produce at least one subtask")
        return v