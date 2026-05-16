from dataclasses import dataclass, field
import concurrent.futures

from datasets.atomic import load_atomic_index
from datasets.convai2 import load_convai2_seeds
from datasets.daily_dialog import load_daily_dialog
from datasets.dialogue import load_dialogue_actions
from datasets.emotion_calib import load_emotion_calibration
from datasets.empathetic import load_empath_index
from datasets.ethics import load_ethics_norms
from datasets.event2mind import load_event2mind
from datasets.mental_chat import load_mental_chat
from datasets.moral_choice import load_moral_choice
from datasets.moral_stories import load_moral_stories
from datasets.okcupid import load_okcupid_essays
from datasets.persona_chat import load_persona_chat
from datasets.social_bias import load_social_bias_norms
from datasets.social_iqa import load_social_iqa_index
from datasets.social_norms import load_social_norms


@dataclass
class DatasetRegistry:
    # Original
    okcupid_essays:      list[str]
    social_norms:        list[str]
    atomic_index:        dict
    social_iqa_index:    dict
    empath_index:        dict
    dialogue_actions:    list[str]
    convai2_seeds:       list[str]
    emotion_calib:       dict
    # New — Behaviour Class: Moral Dilemmas
    moral_stories:       list[dict]   = field(default_factory=list)
    moral_choice:        list[dict]   = field(default_factory=list)
    # New — Emotional Cascades
    event2mind_index:    dict         = field(default_factory=dict)
    # New — Venue-Aware Dialogue Seeds
    daily_dialog_index:  dict         = field(default_factory=dict)
    # New — Mental Health / Deep Support
    mental_chat_index:   dict         = field(default_factory=dict)
    # New — Persona Consistency
    persona_chat:        list[dict]   = field(default_factory=list)
    # New — Social Friction
    social_bias_norms:   list[dict]   = field(default_factory=list)
    # New — Ethics Calibration
    ethics_norms:        dict         = field(default_factory=dict)

    @classmethod
    def load(cls, workers: int = 4) -> "DatasetRegistry":
        loaders = {
            "okcupid_essays":     load_okcupid_essays,
            "social_norms":       load_social_norms,
            "atomic_index":       load_atomic_index,
            "social_iqa_index":   load_social_iqa_index,
            "empath_index":       load_empath_index,
            "dialogue_actions":   load_dialogue_actions,
            "convai2_seeds":      load_convai2_seeds,
            "emotion_calib":      load_emotion_calibration,
            "moral_stories":      load_moral_stories,
            "moral_choice":       load_moral_choice,
            "event2mind_index":   load_event2mind,
            "daily_dialog_index": load_daily_dialog,
            "mental_chat_index":  load_mental_chat,
            "persona_chat":       load_persona_chat,
            "social_bias_norms":  load_social_bias_norms,
            "ethics_norms":       load_ethics_norms,
        }
        _list_keys = {
            "okcupid_essays", "social_norms", "dialogue_actions",
            "convai2_seeds", "moral_stories", "moral_choice",
            "persona_chat", "social_bias_norms",
        }
        results: dict[str, object] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {key: pool.submit(fn) for key, fn in loaders.items()}
            for key, future in futures.items():
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = [] if key in _list_keys else {}

        return cls(
            okcupid_essays=results["okcupid_essays"],
            social_norms=results["social_norms"],
            atomic_index=results["atomic_index"],
            social_iqa_index=results["social_iqa_index"],
            empath_index=results["empath_index"],
            dialogue_actions=results["dialogue_actions"],
            convai2_seeds=results["convai2_seeds"],
            emotion_calib=results["emotion_calib"],
            moral_stories=results["moral_stories"],
            moral_choice=results["moral_choice"],
            event2mind_index=results["event2mind_index"],
            daily_dialog_index=results["daily_dialog_index"],
            mental_chat_index=results["mental_chat_index"],
            persona_chat=results["persona_chat"],
            social_bias_norms=results["social_bias_norms"],
            ethics_norms=results["ethics_norms"],
        )


def load_all_datasets(workers: int = 4) -> DatasetRegistry:
    return DatasetRegistry.load(workers=workers)
