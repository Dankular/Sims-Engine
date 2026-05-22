from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from engine.engine import SimEngine


class PersistenceBackend(Protocol):
    def save_state(self, engine: "SimEngine") -> None: ...

    def load_state(self) -> dict | None: ...

    def restore_engine(self, engine: "SimEngine", state: dict) -> None: ...

    def close(self) -> None: ...
