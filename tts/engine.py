"""
tts/engine.py — Supertonic TTS wrapper.

Assigns a unique voice to the narrator and to each sim, then synthesizes
and plays back speech segments in sequence.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# Voice pool — narrator gets F1, sims cycle through the rest
_NARRATOR_VOICE = "F1"
_SIM_VOICES = ["M1", "M2", "M3", "F2", "F3", "M4", "F4", "M5", "F5"]

_AUDIO_DIR = Path(__file__).parent.parent / "audio"


class TTSEngine:
    """Wraps Supertonic TTS. Lazy-loads the model on first use."""

    def __init__(self, quality: int = 8, speed: float = 1.0, save_audio: bool = True):
        self._quality = quality
        self._speed = speed
        self._save = save_audio
        self._tts = None
        self._lock = threading.Lock()
        self._voice_cache: dict[str, object] = {}
        self._sim_voice_map: dict[str, str] = {}  # sim_name → voice_name
        self._voice_pool = list(_SIM_VOICES)
        self._tick = 0
        if save_audio:
            _AUDIO_DIR.mkdir(exist_ok=True)

    def assign_voices(self, sim_names: list[str]) -> None:
        """Assign a unique TTS voice to each sim."""
        for i, name in enumerate(sim_names):
            if name not in self._sim_voice_map:
                self._sim_voice_map[name] = self._voice_pool[i % len(self._voice_pool)]
        logger.info(
            "Voice assignments: narrator=%s | %s",
            _NARRATOR_VOICE,
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
            self._voice_cache[voice_name] = self._tts.get_voice_style(voice_name=voice_name)
        return self._voice_cache[voice_name]

    def speak(self, speaker: str, text: str, tick: int = 0) -> None:
        """Synthesize and play one segment. Blocks until playback completes."""
        if not text.strip():
            return
        voice_name = (
            _NARRATOR_VOICE if speaker.lower() == "narrator"
            else self._sim_voice_map.get(speaker, _NARRATOR_VOICE)
        )
        with self._lock:
            self._load()
            style = self._get_style(voice_name)
            try:
                wav, duration = self._tts.synthesize(
                    text=text,
                    voice_style=style,
                    total_steps=self._quality,
                    speed=self._speed,
                    lang="en",
                )
            except Exception as exc:
                logger.warning("TTS synthesis failed for %r: %s", text[:60], exc)
                return

            if self._save:
                safe_speaker = speaker.replace(" ", "_").lower()
                out = _AUDIO_DIR / f"tick{tick:03d}_{safe_speaker}.wav"
                self._tts.save_audio(wav, str(out))
                logger.debug("Saved audio: %s", out)

            # Play back: prefer the saved WAV (winsound on Windows), else sounddevice
            played = False
            if self._save and out.exists():
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
        """Play a full script [{speaker, text}, ...] sequentially."""
        for seg in segments:
            self.speak(seg["speaker"], seg["text"], tick=tick)
