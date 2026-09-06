from typing import TypedDict, Optional, Literal

TaskStatus = Literal[
    "created",
    "planning",
    "simulating",
    "waiting_approval",
    "executing",
    "waiting_confirmation",
    "reviewing",
    "completed",
    "retrying",
    "failed",
]

TaskPriority = Literal[
    "low",
    "normal",
    "high",
    "critical",
]

class SubTask(TypedDict):
    tool: str            # e.g. "swap_tool", "bridge_tool", "defi_tool"
    action: str          # e.g. "buy", "deposit", "bridge"
    params: dict

class ExecutionContext(TypedDict):
    wallet_address: str
    chain_id: int
    network: str
    session_key_id: Optional[str]

class SimulationResult(TypedDict):
    estimated_gas: int
    estimated_slippage_bps: int
    risk_score: int
    passed_policy_check: bool
    policy_reason: Optional[str]

class ExecutionResult(TypedDict):
    tx_hash: Optional[str]

    status: Literal[
        "pending",
        "submitted",
        "confirmed",
        "finalized",
        "failed",
    ]

    block_number: Optional[int]
    actual_gas_used: Optional[int]
    confirmations: Optional[int]

    error: Optional[str]

class Critique(TypedDict):
    outcome_matches_intent: bool
    should_retry: bool
    feedback: str

class AgentState(TypedDict):

    # ---------- Identity ----------
    task_id: str
    workflow_id: Optional[str]
    event_id: Optional[str]
    correlation_id: Optional[str]
    agent_id: str

    # ---------- User Request ----------
    user_request: str
    context: dict
    execution_context: ExecutionContext

    # ---------- Planning ----------
    subtasks: list[SubTask]
    current_subtask_index: int

    # ---------- Tool Execution ----------
    tool_results: list[dict]

    # ---------- Execution Pipeline ----------
    simulation: Optional[SimulationResult]
    execution: Optional[ExecutionResult]
    critique: Optional[Critique]

    # ---------- AI ----------
    reasoning_trace: list[str]

    # ---------- Retry ----------
    retry_count: int
    max_retries: int
    errors: list[str]

    # ---------- Enterprise ----------
    priority: TaskPriority
    approval_required: bool
    approved: bool
    metrics: dict

    # ---------- Lifecycle ----------
    created_at: str
    updated_at: Optional[str]

    status: TaskStatus