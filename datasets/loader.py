from dataclasses import dataclass
import concurrent.futures

from datasets.atomic import load_atomic_index
from datasets.convai2 import load_convai2_seeds
from datasets.dialogue import load_dialogue_actions
from datasets.emotion_calib import load_emotion_calibration
from datasets.empathetic import load_empath_index
from datasets.okcupid import load_okcupid_essays
from datasets.social_iqa import load_social_iqa_index
from datasets.social_norms import load_social_norms


@dataclass
class DatasetRegistry:
    okcupid_essays: list[str]
    social_norms: list[str]
    atomic_index: dict
    social_iqa_index: dict
    empath_index: dict
    dialogue_actions: list[str]
    convai2_seeds: list[str]
    emotion_calib: dict

    @classmethod
    def load(cls, workers: int = 3) -> "DatasetRegistry":
        loaders = {
            "okcupid_essays": load_okcupid_essays,
            "social_norms": load_social_norms,
            "atomic_index": load_atomic_index,
            "social_iqa_index": load_social_iqa_index,
            "empath_index": load_empath_index,
            "dialogue_actions": load_dialogue_actions,
            "convai2_seeds": load_convai2_seeds,
            "emotion_calib": load_emotion_calibration,
        }
        results: dict[str, object] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {key: pool.submit(fn) for key, fn in loaders.items()}
            for key, future in futures.items():
                try:
                    results[key] = future.result()
                except Exception:
                    results[key] = (
                        []
                        if key
                        in {
                            "okcupid_essays",
                            "social_norms",
                            "dialogue_actions",
                            "convai2_seeds",
                        }
                        else {}
                    )

        return cls(
            okcupid_essays=results["okcupid_essays"],
            social_norms=results["social_norms"],
            atomic_index=results["atomic_index"],
            social_iqa_index=results["social_iqa_index"],
            empath_index=results["empath_index"],
            dialogue_actions=results["dialogue_actions"],
            convai2_seeds=results["convai2_seeds"],
            emotion_calib=results["emotion_calib"],
        )


def load_all_datasets(workers: int = 3) -> DatasetRegistry:
    return DatasetRegistry.load(workers=workers)
