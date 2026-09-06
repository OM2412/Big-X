from enum import Enum


class TaskStatus(str, Enum):
    PLANNING = "planning"
    SIMULATING = "simulating"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    RETRYING = "retrying"
    DONE = "done"
    FAILED = "failed"


class ExecutionStatus(str, Enum):
    CONFIRMED = "confirmed"
    FAILED = "failed"
