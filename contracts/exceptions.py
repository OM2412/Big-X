class PlanningPipelineError(Exception):
    """Base class — catch this in langgraph_flow.py to route any pipeline
    failure into the Critic's retry logic uniformly."""


class PlannerParseError(PlanningPipelineError):
    """LLM returned malformed or non-JSON output."""


class UnknownToolError(PlanningPipelineError):
    """Planner referenced a tool not in the registry."""
    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        super().__init__(f"Unknown tool: {tool_name}")


class CapabilityDeniedError(PlanningPipelineError):
    """Agent's on-chain capability bitmask doesn't include what this
    subtask requires — checked against AgentRegistry.sol before the
    subtask is even simulated, not just at execution time."""
    def __init__(self, agent_id: str, required_capability: str):
        self.agent_id = agent_id
        self.required_capability = required_capability
        super().__init__(f"Agent {agent_id} lacks capability: {required_capability}")


class MemoryRetrievalError(PlanningPipelineError):
    """Portfolio, history, or RAG lookup failed — distinct from a planning
    failure since the remedy differs (retry the fetch, don't replan)."""


class ToolResolutionError(PlanningPipelineError):
    """A subtask couldn't be translated into a (target, value, calldata)
    call — e.g. the tool rejected the params, or required config is missing."""


class PolicyRejectedError(PlanningPipelineError):
    """PolicyEngine.checkAction() returned false. Distinct from a
    ToolResolutionError — this means the call was built fine, it's just
    not allowed."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Policy rejected: {reason}")


class TransientDependencyError(PlanningPipelineError):
    """RPC call, LLM call, or vector store call failed in a way that might
    succeed on retry — as opposed to a permanent rejection like
    PolicyRejectedError or UnknownToolError."""