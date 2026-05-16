"""
tts/engine.py — Supertonic TTS wrapper.

Assigns a unique voice to the narrator and to each sim, then synthesizes
and plays back speech segments in sequence.
"""

from __future__ import annotations

import logging
import os
import threading
import tempfile
import urllib.request
import inspect
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# ── System 3: Emotion → speech-rate mapping ───────────────────────────────────
# Values are multipliers applied to the base speed before synthesis.
_EMOTION_SPEED: dict[str, float] = {
    "grief":       0.82,
    "sadness":     0.86,
    "remorse":     0.84,
    "love":        0.95,
    "nervousness": 1.06,
    "fear":        1.12,
    "anger":       1.14,
    "excitement":  1.18,
    "joy":         1.12,
    "surprise":    1.10,
    "pride":       1.05,
    "relief":      0.92,
    "confusion":   0.98,
}
_EMOTION_SPEED_DEFAULT = 1.0


def _emotion_speed_modifier(emotion: str) -> float:
    return _EMOTION_SPEED.get(emotion.lower().strip(), _EMOTION_SPEED_DEFAULT)


# Voice pool — narrator gets F1 by default, sims cycle through the rest
_DEFAULT_NARRATOR_VOICE = "F1"
_BUILTIN_VOICES = {"F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"}
_SIM_VOICES = ["M1", "M2", "M3", "F2", "F3", "M4", "F4", "M5", "F5"]

_AUDIO_DIR = Path(__file__).parent.parent / "audio"


class TTSEngine:
    """Wraps Supertonic TTS. Lazy-loads the model on first use."""

    def __init__(
        self,
        quality: int = 8,
        speed: float = 1.0,
        save_audio: bool = True,
        narrator_voice: str | None = None,
    ):
        self._quality = quality
        self._speed = speed
        self._save = save_audio
        self._narrator_voice = (narrator_voice or _DEFAULT_NARRATOR_VOICE).strip()
        self._narrator_style_voice = (
            self._narrator_voice
            if self._narrator_voice in _BUILTIN_VOICES
            else _DEFAULT_NARRATOR_VOICE
        )
        self._tts = None
        self._lock = threading.Lock()
        self._voice_cache: dict[str, object] = {}
        self._sim_voice_map: dict[str, str] = {}  # sim_name → voice_name
        self._voice_pool = list(_SIM_VOICES)
        self._tick = 0
        self._narrator_ref_audio: str | None = None
        if save_audio:
            _AUDIO_DIR.mkdir(exist_ok=True)

    def assign_voices(self, sim_names: list[str]) -> None:
        """Assign a unique TTS voice to each sim."""
        for i, name in enumerate(sim_names):
            if name not in self._sim_voice_map:
                self._sim_voice_map[name] = self._voice_pool[i % len(self._voice_pool)]
        logger.info(
            "Voice assignments: narrator=%s (style=%s) | %s",
            self._narrator_voice,
            self._narrator_style_voice,
            " | ".join(f"{n}={v}" for n, v in self._sim_voice_map.items()),
        )

    def _load(self):
        if self._tts is not None:
            return
        import huggingface_hub.constants as _hf_const
        from supertonic import TTS

        # ocean_scorer.py sets HF_HUB_OFFLINE=1 at import time — unset it
        # so Supertonic can download its ONNX assets, then restore.
        _offline_vars = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
        _saved_env = {k: os.environ.pop(k, None) for k in _offline_vars}
        _saved_flag = _hf_const.HF_HUB_OFFLINE
        _hf_const.HF_HUB_OFFLINE = False
        try:
            logger.info("Loading Supertonic TTS model...")
            self._tts = TTS(auto_download=True)
            logger.info("Supertonic ready.")
        finally:
            _hf_const.HF_HUB_OFFLINE = _saved_flag
            for k, v in _saved_env.items():
                if v is not None:
                    os.environ[k] = v

    def _get_style(self, voice_name: str):
        if voice_name not in self._voice_cache:
            self._load()
            if self._tts is None:
                raise RuntimeError("TTS backend is unavailable")
            self._voice_cache[voice_name] = self._tts.get_voice_style(
                voice_name=voice_name
            )
        return self._voice_cache[voice_name]

    def _resolve_narrator_reference_audio(self) -> str | None:
        if self._narrator_ref_audio is not None:
            return self._narrator_ref_audio
        try:
            from tts.voice_catalog import load_voice_catalog, find_voice_by_id

            voices = load_voice_catalog()
            rec = find_voice_by_id(self._narrator_voice, voices)
            preview_url = str(rec.get("preview_url", "")).strip() if rec else ""
            if not preview_url:
                self._narrator_ref_audio = ""
                return None
            tmp_dir = Path(tempfile.gettempdir()) / "sims_engine_voice_refs"
            tmp_dir.mkdir(exist_ok=True)
            out = tmp_dir / f"{self._narrator_voice}.mp3"
            if not out.exists():
                urllib.request.urlretrieve(preview_url, out)
            self._narrator_ref_audio = str(out)
            return self._narrator_ref_audio
        except Exception:
            self._narrator_ref_audio = ""
            return None

    def speak(self, speaker: str, text: str, tick: int = 0, emotion: str = "") -> None:
        """Synthesize and play one segment. Blocks until playback completes."""
        if not text.strip():
            return
        voice_name = (
            self._narrator_style_voice
            if speaker.lower() == "narrator"
            else self._sim_voice_map.get(speaker, self._narrator_style_voice)
        )
        # System 3: modulate playback speed by current emotion
        effective_speed = self._speed * _emotion_speed_modifier(emotion)
        with self._lock:
            self._load()
            if self._tts is None:
                return
            style = self._get_style(voice_name)
            try:
                extra_kwargs = {}
                if speaker.lower() == "narrator":
                    ref = self._resolve_narrator_reference_audio()
                    if ref:
                        sig = inspect.signature(self._tts.synthesize)
                        if "reference_audio" in sig.parameters:
                            extra_kwargs["reference_audio"] = ref
                        elif "reference_wav" in sig.parameters:
                            extra_kwargs["reference_wav"] = ref
                        elif "speaker_wav" in sig.parameters:
                            extra_kwargs["speaker_wav"] = ref
                wav, duration = self._tts.synthesize(
                    text=text,
                    voice_style=style,
                    total_steps=self._quality,
                    speed=effective_speed,
                    lang="en",
                    **extra_kwargs,
                )
            except Exception as exc:
                logger.warning("TTS synthesis failed for %r: %s", text[:60], exc)
                return

            out = None
            if self._save:
                safe_speaker = speaker.replace(" ", "_").lower()
                out = _AUDIO_DIR / f"tick{tick:03d}_{safe_speaker}.wav"
                self._tts.save_audio(wav, str(out))
                logger.debug("Saved audio: %s", out)

            # Play back: prefer the saved WAV (winsound on Windows), else sounddevice
            played = False
            if self._save and out is not None and out.exists():
                try:
                    import winsound

                    winsound.PlaySound(str(out), winsound.SND_FILENAME)
                    played = True
                except Exception:
                    pass
            if not played:
                try:
                    import sounddevice as sd
                    import numpy as np

                    audio = np.array(wav).flatten()
                    sd.play(audio, samplerate=22050, channels=1, blocking=True)
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
