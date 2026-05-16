from datasets.cache import cache_load


def load_emotion_calibration() -> dict:
    return cache_load("emotion_calib") or {}


def build_emotion_calibration_block() -> str:
    examples = load_emotion_calibration()
    if not examples:
        return ""
    lines = ["EMOTION CALIBRATION:"]
    for label, texts in examples.items():
        if texts:
            lines.append(f"- {label}: {texts[0][:100]}")
    return "\n".join(lines)
