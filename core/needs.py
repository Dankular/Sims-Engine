from dataclasses import dataclass

from config import NEEDS_DECAY, NEED_CRITICAL, NEED_LOW, NEED_NAMES


@dataclass
class Needs:
    hunger: float = 80.0
    energy: float = 80.0
    social: float = 70.0
    fun: float = 65.0
    hygiene: float = 90.0
    environment: float = 75.0
    bladder: float = 85.0
    comfort: float = 80.0

    def tick(self, ocean: dict) -> None:
        extrav = ocean.get("extraversion", 0.5)
        self.hunger = max(0, self.hunger - NEEDS_DECAY)
        self.energy = max(0, self.energy - NEEDS_DECAY * 0.8)
        self.social = max(0, self.social - NEEDS_DECAY * (1.2 if extrav > 0.6 else 0.7))
        self.fun = max(0, self.fun - NEEDS_DECAY * 0.9)
        self.hygiene = max(0, self.hygiene - NEEDS_DECAY * 0.5)
        self.environment = max(0, self.environment - NEEDS_DECAY * 0.3)
        self.bladder = max(0, self.bladder - NEEDS_DECAY * 1.2)
        self.comfort = max(0, self.comfort - NEEDS_DECAY * 0.4)

    def restore(self, need: str, amount: float) -> None:
        current = getattr(self, need, 0)
        setattr(self, need, min(100.0, current + amount))

    def critical_needs(self) -> list[str]:
        return [n for n in NEED_NAMES if getattr(self, n) < NEED_CRITICAL]

    def low_needs(self) -> list[str]:
        return [n for n in NEED_NAMES if NEED_CRITICAL <= getattr(self, n) < NEED_LOW]

    def pressure_vector(self) -> dict[str, float]:
        return {n: max(0, 100 - getattr(self, n)) / 100 for n in NEED_NAMES}
