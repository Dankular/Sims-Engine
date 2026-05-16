import uuid
from dataclasses import dataclass, field
from typing import Any


def new_interaction_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class PendingInteraction:
    sim_a_id: str
    sim_b_id: str
    interaction: str
    rel_key: tuple[str, str]
    future: Any
    tick_submitted: int
    memory_ctx: str
    venue_snapshot: dict
    interaction_id: str = field(default_factory=new_interaction_id)


def drain_pending(
    pending: list[PendingInteraction],
) -> tuple[list[PendingInteraction], list[PendingInteraction]]:
    done, still_pending = [], []
    for item in pending:
        (done if item.future.done() else still_pending).append(item)
    return done, still_pending
