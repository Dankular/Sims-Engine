from dataclasses import dataclass, field

from config import EMOTIONS_27
from sim_types.sim_types import Moodlet


@dataclass
class EmotionState:
    moodlets: list[Moodlet] = field(default_factory=list)
    dominant: str = "neutral"
    dominant_valence: float = 0.5

    POSITIVE = {
        "joy",
        "admiration",
        "amusement",
        "excitement",
        "gratitude",
        "love",
        "optimism",
        "pride",
        "relief",
        "approval",
        "caring",
    }
    NEGATIVE = {
        "anger",
        "annoyance",
        "disappointment",
        "disapproval",
        "disgust",
        "embarrassment",
        "fear",
        "grief",
        "remorse",
        "sadness",
    }

    def add(
        self, label: str, intensity: float, duration: int, source: str = ""
    ) -> None:
        if label not in EMOTIONS_27:
            label = "surprise"
        self.moodlets.append(Moodlet(label, intensity, duration, source))
        self._recalculate()

    def tick(self, ocean: dict) -> None:
        neuro = ocean.get("neuroticism", 0.5)
        surviving: list[Moodlet] = []
        for moodlet in self.moodlets:
            decay = (
                1
                if moodlet.label not in self.NEGATIVE
                else max(1, int(2 * neuro + 0.5))
            )
            moodlet.duration -= decay
            if moodlet.duration > 0:
                surviving.append(moodlet)
        self.moodlets = surviving
        self._recalculate()

    def _recalculate(self) -> None:
        if not self.moodlets:
            self.dominant = "neutral"
            self.dominant_valence = 0.5
            return
        top = max(self.moodlets, key=lambda item: item.intensity)
        self.dominant = top.label
        pos = sum(m.intensity for m in self.moodlets if m.label in self.POSITIVE)
        neg = sum(m.intensity for m in self.moodlets if m.label in self.NEGATIVE)
        total = pos + neg + 0.001
        self.dominant_valence = round(pos / total, 2)
