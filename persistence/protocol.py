from typing import Protocol


class PersistenceBackend(Protocol):
    def save_state(self, engine: "SimEngine") -> None: ...

    def close(self) -> None: ...
