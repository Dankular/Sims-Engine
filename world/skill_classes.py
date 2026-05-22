from __future__ import annotations

from dataclasses import dataclass, field
import random


EDU_VENUES = {
    "university": {
        "capacity": 20,
        "prestige": 1.2,
        "equipment": {"whiteboard", "computers", "scientific_tools"},
    },
    "training_center": {
        "capacity": 12,
        "prestige": 1.0,
        "equipment": {"whiteboard", "exercise_equipment"},
    },
    "workshop": {
        "capacity": 10,
        "prestige": 0.95,
        "equipment": {"crafting_stations", "whiteboard"},
    },
    "studio": {
        "capacity": 14,
        "prestige": 1.1,
        "equipment": {"instruments", "whiteboard"},
    },
    "business_school": {
        "capacity": 16,
        "prestige": 1.15,
        "equipment": {"whiteboard", "computers"},
    },
}

SKILL_OBJECT_REQUIREMENTS = {
    "programming": {"computers"},
    "logic": {"whiteboard"},
    "fitness": {"exercise_equipment"},
    "painting": {"crafting_stations"},
    "guitar": {"instruments"},
    "cooking": {"crafting_stations"},
    "charisma": {"whiteboard"},
}


@dataclass
class SkillClass:
    class_id: str
    skill: str
    duration: int
    cost: float
    instructor_id: str
    location: str
    xp_reward: float
    requirements: dict
    start_tick: int
    end_tick: int
    recurrence: int = 24
    max_attendance: int = 10
    repeatable: bool = True
    attendees: list[str] = field(default_factory=list)


@dataclass
class ClassProgress:
    completion_percent: float = 0.0
    paid_status: bool = False
    saved_progress: float = 0.0


class SkillClassSystem:
    def __init__(self) -> None:
        self.classes: dict[str, SkillClass] = {}
        self.progress: dict[tuple[str, str], ClassProgress] = {}
        self.certifications: dict[str, dict[str, int]] = {}
        self.institution_reputation: dict[str, float] = {k: 0.0 for k in EDU_VENUES}
        self.lecture_history: list[dict] = []
        self._seed_defaults()

    def _seed_defaults(self) -> None:
        default = [
            ("programming", "business_school", 4, 140.0, 0.22),
            ("fitness", "training_center", 3, 90.0, 0.20),
            ("logic", "university", 4, 120.0, 0.21),
            ("painting", "workshop", 3, 85.0, 0.18),
            ("charisma", "studio", 3, 95.0, 0.19),
        ]
        for i, (skill, venue, duration, cost, xp) in enumerate(default, start=1):
            cid = f"class_{i}_{skill}"
            self.classes[cid] = SkillClass(
                class_id=cid,
                skill=skill,
                duration=duration,
                cost=cost,
                instructor_id="",
                location=venue,
                xp_reward=xp,
                requirements={
                    "objects": list(
                        SKILL_OBJECT_REQUIREMENTS.get(skill, {"whiteboard"})
                    )
                },
                start_tick=i * 3,
                end_tick=i * 3 + duration,
                recurrence=36,
                max_attendance=EDU_VENUES[venue]["capacity"],
            )

    def tick(self, engine) -> None:
        now = engine.tick_count
        self._schedule_rollover(now)
        self._npc_enrollments(engine, now)
        self._run_active_classes(engine, now)
        self._host_lectures(engine, now)

    def _schedule_rollover(self, now: int) -> None:
        for cls in self.classes.values():
            if now > cls.end_tick:
                cls.start_tick += cls.recurrence
                cls.end_tick = cls.start_tick + cls.duration
                cls.attendees.clear()

    def _npc_enrollments(self, engine, now: int) -> None:
        for sim in engine.sims:
            for cls in self.classes.values():
                if not (cls.start_tick - 2 <= now <= cls.start_tick):
                    continue
                if len(cls.attendees) >= cls.max_attendance:
                    continue
                if sim.simoleons < cls.cost:
                    continue
                if sim.sim_id in cls.attendees:
                    continue
                lvl = sim.skills.levels.get(cls.skill, 0.0)
                if lvl >= 9.8:
                    continue
                if random.random() < self._enrollment_interest(sim, cls):
                    sim.simoleons -= cls.cost
                    cls.attendees.append(sim.sim_id)
                    key = (sim.sim_id, cls.class_id)
                    p = self.progress.setdefault(key, ClassProgress())
                    p.paid_status = True

    def _enrollment_interest(self, sim, cls: SkillClass) -> float:
        base = 0.08
        base += max(0.0, (100.0 - sim.needs.fun) / 500.0)
        base += max(0.0, (100.0 - sim.needs.social) / 450.0)
        if cls.skill in {
            "charisma",
            "programming",
            "logic",
        } and "genius" in sim.profile.get("traits", []):
            base += 0.08
        if "lazy" in sim.profile.get("traits", []) and cls.skill in {"fitness"}:
            base -= 0.06
        return max(0.02, min(0.45, base))

    def _run_active_classes(self, engine, now: int) -> None:
        for cls in self.classes.values():
            if not (cls.start_tick <= now <= cls.end_tick):
                continue
            venue = EDU_VENUES[cls.location]
            xp_mod = venue["prestige"]
            for sid in list(cls.attendees):
                sim = engine._sim_lookup.get(sid)
                if not sim:
                    continue
                key = (sid, cls.class_id)
                p = self.progress.setdefault(key, ClassProgress())
                step = 100.0 / max(1, cls.duration)
                p.completion_percent = min(100.0, p.completion_percent + step)
                p.saved_progress = p.completion_percent

                focus_bonus = 1.0
                if sim.emotion.dominant in {"focus", "optimism", "inspiration"}:
                    focus_bonus += 0.15
                if hasattr(sim, "wellness_state"):
                    focus_bonus += max(
                        0.0,
                        (sim.wellness_state.get("focus_modifier", 1.0) - 1.0) * 0.25,
                    )

                sim.skills.gain_xp(cls.skill, cls.xp_reward * xp_mod * focus_bonus)
                sim.skills.gain_xp("charisma", 0.02)
                sim.emotion.add("focus", 0.15, duration=1, source=f"class:{cls.skill}")
                sim.needs.energy = max(0.0, sim.needs.energy - 0.4)

                if p.completion_percent >= 100.0:
                    self._award_certificate(sim, cls.skill)
                    p.completion_percent = 0.0

            self._class_socialization(engine, cls)

    def _class_socialization(self, engine, cls: SkillClass) -> None:
        ids = cls.attendees
        if len(ids) < 2:
            return
        a = engine._sim_lookup.get(random.choice(ids))
        b = (
            engine._sim_lookup.get(random.choice([i for i in ids if i != a.sim_id]))
            if a
            else None
        )
        if not a or not b:
            return
        rel = engine.relationships.get(a.sim_id, b.sim_id)
        rel.apply_deltas(0.3, 0.0)

    def _award_certificate(self, sim, skill: str) -> None:
        certs = self.certifications.setdefault(sim.sim_id, {})
        rank = certs.get(skill, 0) + 1
        certs[skill] = min(5, rank)
        sim.reputation_score = min(100.0, sim.reputation_score + 0.4)
        sim.career_performance = min(100.0, sim.career_performance + 0.25 * rank)
        sim.emotion.add("proud", 0.25, duration=3, source=f"certificate:{skill}:{rank}")

    def _host_lectures(self, engine, now: int) -> None:
        if now % 10 != 0:
            return
        instructors = [
            s for s in engine.sims if s.skills.levels.get("charisma", 0.0) >= 4.0
        ]
        if not instructors:
            return
        host = random.choice(instructors)
        skill = max(host.skills.levels, key=lambda k: host.skills.levels.get(k, 0.0))
        attendees = random.sample(engine.sims, k=min(5, len(engine.sims)))
        engagement = 0.5 + host.skills.levels.get("charisma", 0.0) / 20.0
        payout = max(0.0, 30.0 * engagement)
        _eng = getattr(host, "_engine_ref", None)
        if _eng:
            from persistence.ledger import TX_SKILL_CLASS_HOST
            _eng._tx(host, payout, TX_SKILL_CLASS_HOST, description="skill class host fee")
        else:
            host.simoleons += payout
        host.reputation_score = min(100.0, host.reputation_score + 0.5)
        self.lecture_history.append(
            {
                "tick": now,
                "host": host.sim_id,
                "topic": skill,
                "attendees": [a.sim_id for a in attendees],
                "engagement": round(engagement, 3),
                "payout": round(payout, 2),
            }
        )
        for s in attendees:
            if s.sim_id == host.sim_id:
                continue
            s.skills.gain_xp(skill, 0.04 * engagement)
            rel = engine.relationships.get(host.sim_id, s.sim_id)
            rel.apply_deltas(0.25, 0.0)

    def classes_state(self) -> list[dict]:
        out = []
        for c in self.classes.values():
            out.append(
                {
                    "class_id": c.class_id,
                    "skill": c.skill,
                    "duration": c.duration,
                    "cost": round(c.cost, 2),
                    "location": c.location,
                    "xp_reward": round(c.xp_reward, 3),
                    "requirements": dict(c.requirements),
                    "start_tick": c.start_tick,
                    "end_tick": c.end_tick,
                    "max_attendance": c.max_attendance,
                    "attendees": list(c.attendees),
                }
            )
        return out

    def certificates_for(self, sim_id: str) -> dict[str, int]:
        return dict(self.certifications.get(sim_id, {}))
