from __future__ import annotations

from dataclasses import dataclass, field


IDEAL_CONFLICTS: dict[str, set[str]] = {
    "caring": {"devious", "vicious"},
    "devious": {"caring"},
    "diplomatic": {"vicious", "jolly"},
    "vicious": {"caring", "diplomatic"},
    "connoisseur": {"nature-loving", "hardworking"},
    "nature-loving": {"connoisseur"},
    "jolly": {"mysterious", "diplomatic"},
    "mysterious": {"jolly"},
}


@dataclass
class DynastyPerk:
    perk_id: str
    level: int = 1


@dataclass
class Dynasty:
    dynasty_id: str
    name: str
    description: str = ""
    crest: dict[str, str] = field(default_factory=dict)
    head_id: str = ""
    heir_id: str = ""
    member_ids: list[str] = field(default_factory=list)
    outcast_ids: list[str] = field(default_factory=list)
    ideals: list[str] = field(default_factory=list)
    focus_skills: list[str] = field(default_factory=list)
    alliances: set[str] = field(default_factory=set)
    rivalries: set[str] = field(default_factory=set)
    prestige_points: float = 0.0
    prestige_level: int = 1
    unity: float = 50.0
    perk_points: int = 0
    perks: dict[str, DynastyPerk] = field(default_factory=dict)
    scandals: list[dict] = field(default_factory=list)

    def state(self) -> dict:
        return {
            "id": self.dynasty_id,
            "name": self.name,
            "description": self.description,
            "crest": dict(self.crest),
            "head_id": self.head_id,
            "heir_id": self.heir_id,
            "members": list(self.member_ids),
            "outcasts": list(self.outcast_ids),
            "ideals": list(self.ideals),
            "focus_skills": list(self.focus_skills),
            "alliances": sorted(self.alliances),
            "rivalries": sorted(self.rivalries),
            "prestige_points": round(self.prestige_points, 2),
            "prestige_level": int(self.prestige_level),
            "unity": round(self.unity, 1),
            "perk_points": int(self.perk_points),
            "perks": {k: v.level for k, v in self.perks.items()},
            "scandals": list(self.scandals[-20:]),
        }
