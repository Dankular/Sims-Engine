import random

from config import URBANSOUND_CLASS_PROPS, VENUES


class AudioEnvironmentSensor:
    def __init__(self):
        self._classes = list(URBANSOUND_CLASS_PROPS.keys())
        self.current_class = ""
        self.current_props: dict = {}

    def sense(self) -> dict:
        self.current_class = random.choice(self._classes)
        self.current_props = URBANSOUND_CLASS_PROPS[self.current_class].copy()
        return {"ambient_sound": self.current_class, **self.current_props}


__all__ = ["VENUES", "AudioEnvironmentSensor"]
