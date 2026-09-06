from typing import TypedDict, Optional, Literal


Environment = Literal[
    "development",
    "staging",
    "production",
]

Network = Literal[
    "ethereum",
    "polygon",
    "arbitrum",
    "optimism",
    "base",
    "avalanche",
    "bsc",
]

ExecutionMode = Literal[
    "simulation",
    "live",
]

TransactionPriority = Literal[
    "low",
    "normal",
    "high",
    "urgent",
]


class ExecutionContext(TypedDict):
    task_id: str
    workflow_id: Optional[str]
    correlation_id: str
    execution_id: str

    agent_id: str
    user_id: str
    organization_id: Optional[str]

    wallet_address: str
    session_key_id: Optional[str]

    network: Network
    chain_id: int

    execution_mode: ExecutionMode

    nonce: Optional[int]
    gas_budget: Optional[int]
    max_gas_price: Optional[int]

    tx_priority: TransactionPriority

    approval_required: bool
    approved: bool

    risk_score: Optional[int]

    current_step: int
    total_steps: int

    retry_count: int
    max_retries: int

    event_id: Optional[str]
    parent_event_id: Optional[str]

    environment: Environment

    request_ip: Optional[str]
    user_agent: Optional[str]

    metadata: dict

    created_at: str
    expires_at: Optional[str]