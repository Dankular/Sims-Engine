from __future__ import annotations

from dataclasses import dataclass
import random


@dataclass
class SoftwareProject:
    project_id: str
    project_type: str
    complexity: float
    progress: float = 0.0
    quality: float = 0.0
    royalties: float = 0.0
    active: bool = True


class ProgrammingSystem:
    def __init__(self) -> None:
        self.projects: dict[str, list[SoftwareProject]] = {}

    def tick(self, engine) -> None:
        for sim in engine.sims:
            self._progress_long_tasks(sim)
            self._passive_royalties(sim)
            self._freelance_jobs(sim)
            self._hack_attempts(sim)

    def _focus_multiplier(self, sim) -> float:
        base = 1.0
        if sim.emotion.dominant in {"focus", "optimism", "inspiration"}:
            base += 0.18
        if sim.needs.energy < 25:
            base -= 0.2
        if "genius" in sim.profile.get("traits", []):
            base += 0.12
        if "geek" in sim.profile.get("traits", []):
            base += 0.08
        return max(0.5, min(1.8, base))

    def _progress_long_tasks(self, sim) -> None:
        lvl = sim.skills.levels.get("programming", 0.0)
        if lvl < 1:
            return
        bucket = self.projects.setdefault(sim.sim_id, [])
        if not bucket and random.random() < 0.05:
            ptype = random.choice(
                [
                    "plugin",
                    "mod",
                    "mobile_app",
                    "computer_game",
                    "utility_tool",
                    "virus",
                ]
            )
            bucket.append(
                SoftwareProject(
                    project_id=f"proj_{sim.sim_id}_{random.randint(1000, 9999)}",
                    project_type=ptype,
                    complexity=random.uniform(25.0, 120.0),
                )
            )
        focus = self._focus_multiplier(sim)
        for proj in bucket:
            if not proj.active:
                continue
            speed = (1.5 + lvl * 0.5) * focus
            proj.progress = min(100.0, proj.progress + speed / proj.complexity * 100.0)
            sim.skills.gain_xp("programming", 0.08 * focus)
            if proj.progress >= 100.0:
                proj.active = False
                proj.quality = min(100.0, 40.0 + lvl * 5.0 + random.uniform(-10, 15))
                proj.royalties = max(0.0, proj.quality * random.uniform(0.15, 0.45))
                sim.emotion.add(
                    "proud", 0.35, duration=3, source=f"project:{proj.project_type}"
                )

    def _passive_royalties(self, sim) -> None:
        income = 0.0
        for proj in self.projects.get(sim.sim_id, []):
            if proj.royalties <= 0:
                continue
            payout = proj.royalties * random.uniform(0.85, 1.05)
            proj.royalties = max(0.0, proj.royalties * 0.98)
            income += payout
        if income > 0:
            sim.simoleons += income

    def _freelance_jobs(self, sim) -> None:
        lvl = sim.skills.levels.get("programming", 0.0)
        if lvl < 5:
            return
        if random.random() < 0.03:
            payout = 40 + lvl * random.uniform(12, 28)
            success = random.random() < min(0.95, 0.55 + lvl / 20.0)
            if success:
                sim.simoleons += payout
                sim.reputation_score = min(100.0, sim.reputation_score + 0.4)
            else:
                sim.reputation_score = max(-100.0, sim.reputation_score - 0.3)

    def _hack_attempts(self, sim) -> None:
        lvl = sim.skills.levels.get("programming", 0.0)
        if lvl < 3:
            return
        if random.random() < 0.02:
            reward = random.uniform(20.0, 120.0) * (lvl / 5.0)
            trace_risk = max(0.05, 0.35 - lvl * 0.02)
            if random.random() < (0.45 + lvl * 0.04):
                sim.simoleons += reward
                sim.hacker_reputation = min(
                    100.0, getattr(sim, "hacker_reputation", 0.0) + 1.0
                )
            elif random.random() < trace_risk:
                sim.simoleons = max(0.0, sim.simoleons - reward * 0.5)
                sim.reputation_score = max(-100.0, sim.reputation_score - 1.0)

    def project_state(self, sim_id: str) -> list[dict]:
        return [
            {
                "id": p.project_id,
                "type": p.project_type,
                "complexity": round(p.complexity, 2),
                "progress": round(p.progress, 2),
                "quality": round(p.quality, 2),
                "royalties": round(p.royalties, 2),
                "active": p.active,
            }
            for p in self.projects.get(sim_id, [])
        ]
