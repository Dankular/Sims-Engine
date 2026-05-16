from enum import IntEnum


class LODTier(IntEnum):
    ACTIVE = 1
    BACKGROUND = 2
    DORMANT = 3


class SchedulePhase(str):
    WORK = "work"
    SOCIAL = "social"
    HOME = "home"
