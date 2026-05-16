from dataclasses import dataclass
from typing import Optional


@dataclass
class Moodlet:
    label: str
    intensity: float
    duration: int
    source: str = ""


@dataclass
class Want:
    description: str
    target_sim: Optional[str]
    need_linked: Optional[str]
    priority: float


@dataclass
class Fear:
    label: str
    severity: float
