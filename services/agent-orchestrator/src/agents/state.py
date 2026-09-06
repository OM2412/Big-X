from typing import TypedDict


class SubTask(TypedDict):
    tool: str
    action: str
    params: dict


class Critique(TypedDict):
    outcome_matches_intent: bool
    should_retry: bool
    feedback: str


class ExecutionResult(TypedDict):
    tx_hash: str | None
    status: str
    actual_gas_used: int | None
    error: str | None


class SimulationResult(TypedDict):
    estimated_gas: int
    estimated_slippage_bps: int
    risk_score: int
    passed_policy_check: bool
    policy_reason: str


class AgentState(TypedDict, total=False):
    task_id: str
    agent_id: str
    user_request: str
    subtasks: list[SubTask]
    current_subtask_index: int
    status: str
    context: dict
    simulation: SimulationResult | None
    execution: ExecutionResult | None
    critique: Critique | None
    retry_count: int
    max_retries: int
