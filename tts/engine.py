"""
tts/engine.py — OmniVoice TTS wrapper.

Replaces Supertonic with Prince-1/OmniVoice-Onnx.  Uses voice-design
(instruct=) mode so no reference audio files are required — each of the
10 voice slots (M1-M5 / F1-F5) is described via a text persona and
synthesized on demand.

System 3 emotion-driven speed modulation is wired in.
"""

from __future__ import annotations

import logging
import os
import threading
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# ── System 3: Emotion → speech-rate multipliers ───────────────────────────────
_EMOTION_SPEED: dict[str, float] = {
    "grief": 0.82,
    "sadness": 0.86,
    "remorse": 0.84,
    "love": 0.95,
    "nervousness": 1.06,
    "fear": 1.12,
    "anger": 1.14,
    "excitement": 1.18,
    "joy": 1.12,
    "surprise": 1.10,
    "pride": 1.05,
    "relief": 0.92,
    "confusion": 0.98,
}


def _emotion_speed_modifier(emotion: str) -> float:
    return _EMOTION_SPEED.get(emotion.lower().strip(), 1.0)


# Gap 9: num_steps delta per emotion (positive = crisper, negative = breathier/softer)
_EMOTION_STEPS_DELTA: dict[str, int] = {
    "grief": -10,
    "sadness": -8,
    "remorse": -6,
    "love": -2,
    "anger": +6,
    "excitement": +6,
    "joy": +4,
    "pride": +4,
    "surprise": +4,
    "nervousness": +2,
}


def _emotion_steps_delta(emotion: str) -> int:
    return _EMOTION_STEPS_DELTA.get(emotion.lower().strip(), 0)


# ── Voice pool ────────────────────────────────────────────────────────────────
# Each slot gets a text description fed to OmniVoice's instruct= parameter.
# The descriptions are distinct enough for the model to produce clearly
# different-sounding voices without any reference audio files.
VOICE_INSTRUCT: dict[str, str] = {
    "M1": "male, moderate pitch, american accent, middle-aged",
    "M2": "male, low pitch, british accent, middle-aged",
    "M3": "male, high pitch, american accent, young adult",
    "M4": "male, moderate pitch, australian accent, young adult",
    "M5": "male, very low pitch, american accent, elderly",
    "F1": "female, moderate pitch, american accent, middle-aged",
    "F2": "female, high pitch, american accent, young adult",
    "F3": "female, low pitch, british accent, middle-aged",
    "F4": "female, moderate pitch, australian accent, young adult",
    "F5": "female, very high pitch, british accent, young adult",
}

_NARRATOR_VOICE = "F1"
_SIM_VOICE_POOL = ["M1", "M2", "M3", "F2", "F3", "M4", "F4", "M5", "F5"]
_AUDIO_DIR = Path(__file__).parent.parent / "audio"
_SAMPLE_RATE = 24_000  # OmniVoice output sample rate


class TTSEngine:
    """OmniVoice TTS — lazy-loads on first speak() call."""

    def __init__(
        self,
        speed: float = 1.0,
        save_audio: bool = True,
        narrator_voice: str | None = None,
        num_steps: int = 32,
        device: str = "cpu",
    ):
        self._speed = speed
        self._save = save_audio
        self._narrator_voice = (narrator_voice or _NARRATOR_VOICE).strip()
        self._num_steps = num_steps
        self._device = device
        self._model = None
        self._lock = threading.Lock()
        self._sim_voice_map: dict[str, str] = {}  # sim_name → voice slot
        self._voice_pool = list(_SIM_VOICE_POOL)
        if save_audio:
            _AUDIO_DIR.mkdir(exist_ok=True)

    def assign_voices(self, sim_names: list[str]) -> None:
        """Assign a unique voice slot to each sim (round-robin from pool)."""
        for i, name in enumerate(sim_names):
            if name not in self._sim_voice_map:
                self._sim_voice_map[name] = self._voice_pool[i % len(self._voice_pool)]
        logger.info(
            "Voice assignments: narrator=%s | %s",
            self._narrator_voice,
            " | ".join(f"{n}={v}" for n, v in self._sim_voice_map.items()),
        )

    def _load(self) -> None:
        if self._model is not None:
            return

        # ocean_scorer sets HF_HUB_OFFLINE=1 — unset it so the model can download
        import huggingface_hub.constants as _hf_const

        _offline_vars = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
        _saved_env = {k: os.environ.pop(k, None) for k in _offline_vars}
        _saved_flag = _hf_const.HF_HUB_OFFLINE
        _hf_const.HF_HUB_OFFLINE = False

        try:
            from omnivoice import OmniVoice

            # Try the ONNX-optimised variant first, fall back to base model
            for repo in ("Prince-1/OmniVoice-Onnx", "k2-fsa/OmniVoice"):
                try:
                    logger.info("Loading OmniVoice from %s ...", repo)
                    kwargs: dict = {}
                    try:
                        import torch

                        dtype = torch.float32  # float16 is slow on CPU
                        if self._device.startswith("cuda"):
                            dtype = torch.float16
                        kwargs = {"device_map": self._device, "dtype": dtype}
                    except ImportError:
                        pass
                    self._model = OmniVoice.from_pretrained(repo, **kwargs)
                    logger.info("OmniVoice ready (%s).", repo)
                    return
                except Exception as exc:
                    logger.warning("OmniVoice from %s failed: %s", repo, exc)

            raise RuntimeError("Could not load OmniVoice from any known repo.")

        finally:
            _hf_const.HF_HUB_OFFLINE = _saved_flag
            for k, v in _saved_env.items():
                if v is not None:
                    os.environ[k] = v

    def _synthesize(
        self, text: str, voice_slot: str, speed: float, num_steps: int | None = None
    ) -> "np.ndarray | None":
        """Run OmniVoice inference. Returns a mono float32 array or None."""
        instruct = VOICE_INSTRUCT.get(voice_slot, VOICE_INSTRUCT["M1"])
        steps = num_steps if num_steps is not None else self._num_steps
        try:
            result = self._model.generate(
                text=text,
                instruct=instruct,
                speed=speed,
                num_step=steps,
            )
            # generate() returns a list of arrays or a single array
            if isinstance(result, (list, tuple)):
                audio = result[0]
            else:
                audio = result
            import numpy as np

            return np.array(audio, dtype=np.float32).flatten()
        except TypeError:
            # Some versions don't accept instruct= — fall back to ref_audio=None
            try:
                result = self._model.generate(text=text, speed=speed, num_step=steps)
                import numpy as np

                arr = result[0] if isinstance(result, (list, tuple)) else result
                return np.array(arr, dtype=np.float32).flatten()
            except Exception as exc2:
                logger.warning("OmniVoice synthesis fallback failed: %s", exc2)
                return None
        except Exception as exc:
            logger.warning("OmniVoice synthesis failed: %s", exc)
            return None

    def speak(self, speaker: str, text: str, tick: int = 0, emotion: str = "") -> None:
        """Synthesize and play one segment. Blocks until playback completes."""
        if not text.strip():
            return

        voice_slot = (
            self._narrator_voice
            if speaker.lower() == "narrator"
            else self._sim_voice_map.get(speaker, self._narrator_voice)
        )
        effective_speed = self._speed * _emotion_speed_modifier(emotion)
        effective_steps = max(8, self._num_steps + _emotion_steps_delta(emotion))

        with self._lock:
            self._load()
            if self._model is None:
                return

            audio = self._synthesize(text, voice_slot, effective_speed, effective_steps)
            if audio is None:
                return

            out: Path | None = None
            if self._save:
                safe = speaker.replace(" ", "_").lower()
                out = _AUDIO_DIR / f"tick{tick:03d}_{safe}.wav"
                try:
                    import soundfile as sf

                    sf.write(str(out), audio, _SAMPLE_RATE)
                    logger.debug("Saved audio: %s", out)
                except Exception as exc:
                    logger.warning("Could not save audio: %s", exc)
                    out = None

            # Playback — prefer winsound (Windows WAV), fall back to sounddevice
            played = False
            if out is not None and out.exists():
                try:
                    import winsound

                    winsound.PlaySound(str(out), winsound.SND_FILENAME)
                    played = True
                except Exception:
                    pass

            if not played:
                try:
                    import sounddevice as sd

                    sd.play(audio, samplerate=_SAMPLE_RATE, channels=1, blocking=True)
                except Exception as exc:
                    logger.warning("Audio playback failed: %s", exc)

    def speak_script(self, segments: list[dict], tick: int = 0) -> None:
        """Play a full script [{speaker, text, ?emotion}, ...] sequentially."""
        for seg in segments:
            self.speak(
                seg["speaker"],
                seg["text"],
                tick=tick,
                emotion=seg.get("emotion", ""),
            )
