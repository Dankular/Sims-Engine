from config import SCHEDULE_SOCIAL, SCHEDULE_WORK


def time_label(hour: int) -> str:
    h = hour % 24
    if 5 <= h < 9:
        return "morning"
    if 9 <= h < 12:
        return "late morning"
    if 12 <= h < 14:
        return "noon"
    if 14 <= h < 18:
        return "afternoon"
    if 18 <= h < 21:
        return "evening"
    if 21 <= h < 24:
        return "night"
    return "late night"


__all__ = ["time_label", "SCHEDULE_WORK", "SCHEDULE_SOCIAL"]
