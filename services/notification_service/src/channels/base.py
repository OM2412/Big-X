from typing import Optional, Callable, Any
from abc import ABC, abstractmethod

class NotificationChannel(ABC):
    def __init__(self, metrics: Optional[Callable[..., Any]] = None):
        self.metrics = metrics

    @abstractmethod
    async def send(self, *args: Any, **kwargs: Any) -> None:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

class PermanentFailure(Exception):
    pass

class TransientFailure(Exception):
    pass

MetricsHook = Optional[Callable[..., Any]]
