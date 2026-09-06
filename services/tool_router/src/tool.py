from dataclasses import dataclass
from typing import Optional


@dataclass
class ToolCall:
    target: str
    value: int
    calldata: bytes


class Tool:
    name: str = ""
