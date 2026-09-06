import logging
from typing import Awaitable, Callable
 
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.state import CompiledStateGraph

from state import AgentState
from exceptions import PlanningPipelineError
from planning_pipeline import Dependencies, PlannerAgent, MemoryKnowledgeAgent, SimulatorAgent
from execution_review import ExecutorAgent, CriticAgent
from state_enums import TaskStatus

logger = logging.getLogger(__name__)

PLANNER_NODE = "planner"
MEMORY_NODE = "memory"
SIMULATOR_NODE = "simulator"
EXECUTOR_NODE = "executor"
CRITIC_NODE = "critic"


def route_after_critic(state: AgentState) -> str:
    """Route workflow after Critic evaluation."""

    status = state["status"]

    if status == TaskStatus.SIMULATING:
        return "simulator"

    if status == TaskStatus.RETRYING:
        return "planner"

    return END


def _wrap_node(
    node_fn: Callable[[AgentState], Awaitable[AgentState]],
):
    """Converts a PlanningPipelineError (PolicyRejectedError,
    CapabilityDeniedError, etc.) raised inside a node into a failed
    state instead of an uncaught exception — LangGraph nodes are
    expected to return state, not raise."""
    async def wrapped(state: AgentState) -> AgentState:
        try:
            return await node_fn(state)

        except PlanningPipelineError as exc:
            logger.warning(
                "Pipeline error in %s: %s",
                node_fn.__name__,
                exc,
            )

            state["status"] = TaskStatus.FAILED
            state["critique"] = {
                "outcome_matches_intent": False,
                "should_retry": False,
                "feedback": str(exc),
            }

            return state

        except Exception as exc:
            logger.exception(
                "Unexpected error in %s for task %s",
                node_fn.__name__,
                state.get("task_id", "unknown"),
            )

            state["status"] = TaskStatus.FAILED
            state["critique"] = {
                "outcome_matches_intent": False,
                "should_retry": False,
                "feedback": f"Unexpected error: {exc}",
            }

            return state

    return wrapped


def build_agent_graph(
    deps: Dependencies,
) -> CompiledStateGraph:
    """Compiles the LangGraph state machine once at service startup;
    reuse the compiled graph across requests, don't rebuild per task."""
    planner = PlannerAgent(deps)
    memory = MemoryKnowledgeAgent(deps)
    simulator = SimulatorAgent(deps)
    executor = ExecutorAgent(deps)
    critic = CriticAgent(deps)

    graph = StateGraph(AgentState)
    graph.add_node(PLANNER_NODE, _wrap_node(planner.run))
    graph.add_node(MEMORY_NODE, _wrap_node(memory.run))
    graph.add_node(SIMULATOR_NODE, _wrap_node(simulator.run))
    graph.add_node(EXECUTOR_NODE, _wrap_node(executor.run))
    graph.add_node(CRITIC_NODE, _wrap_node(critic.run))

    graph.set_entry_point(PLANNER_NODE)
    graph.add_edge(PLANNER_NODE, MEMORY_NODE)
    graph.add_edge(MEMORY_NODE, SIMULATOR_NODE)
    graph.add_edge(SIMULATOR_NODE, EXECUTOR_NODE)
    graph.add_edge(EXECUTOR_NODE, CRITIC_NODE)

    graph.add_conditional_edges(
        CRITIC_NODE,
        route_after_critic,
        {
            SIMULATOR_NODE: SIMULATOR_NODE,
            PLANNER_NODE: PLANNER_NODE,
            END: END,
        },
    )

    checkpointer = MemorySaver()  # Dev-only; replace with Postgres/Redis/SQLite for production

    return graph.compile(
        checkpointer=checkpointer,
    )


def build_initial_state(
    task_id: str,
    agent_id: str,
    user_request: str,
    max_retries: int = 2,
) -> AgentState:
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
        "status": TaskStatus.PLANNING,
    }