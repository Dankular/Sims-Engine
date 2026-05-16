from collections import defaultdict
from typing import Any, Callable


class EventBus:
    def __init__(self):
        self._handlers: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._handlers[event].append(handler)

    def emit(self, event: str, **data) -> None:
        for handler in self._handlers.get(event, []):
            handler(**data)
