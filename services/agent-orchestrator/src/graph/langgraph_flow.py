import logging

from langgraph.graph import StateGraph, END

from ..agents.state import AgentState
from ..agents.planner import PlannerAgent
from ..agents.memory_knowledge import MemoryKnowledgeAgent
from ..agents.simulator import SimulatorAgent
from ..agents.executor import ExecutorAgent
from ..agents.critic import CriticAgent

logger = logging.getLogger(__name__)


def route_after_critic(state: AgentState) -> str:
    """Decides where the graph goes after Critic reviews an execution."""
    status = state["status"]

    if status == "simulating":
        return "simulator"   # more subtasks remain in the current plan
    if status == "retrying":
        return "planner"      # transient failure — full replan via feedback loop
    return END                # "done" or "failed" — nothing more to do


def build_agent_graph(
    planner: PlannerAgent,
    memory: MemoryKnowledgeAgent,
    simulator: SimulatorAgent,
    executor: ExecutorAgent,
    critic: CriticAgent,
):
    """Compiles the LangGraph state machine. Call once at service startup
    and reuse the compiled graph across requests — don't rebuild per task."""

    graph = StateGraph(AgentState)

    graph.add_node("planner", planner.run)
    graph.add_node("memory", memory.run)
    graph.add_node("simulator", simulator.run)
    graph.add_node("executor", executor.run)
    graph.add_node("critic", critic.run)

    graph.set_entry_point("planner")
    graph.add_edge("planner", "memory")
    graph.add_edge("memory", "simulator")
    graph.add_edge("simulator", "executor")
    graph.add_edge("executor", "critic")

    graph.add_conditional_edges(
        "critic",
        route_after_critic,
        {"simulator": "simulator", "planner": "planner", END: END},
    )

    return graph.compile()


def build_initial_state(task_id: str, agent_id: str, user_request: str, max_retries: int = 2) -> AgentState:
    return {
        "task_id": task_id,
        "agent_id": agent_id,
        "user_request": user_request,
        "context": {},
        "subtasks": [],
        "current_subtask_index": 0,
        "simulation": None,
        "execution": None,
        "critique": None,
        "retry_count": 0,
        "max_retries": max_retries,
        "status": "planning",
    }