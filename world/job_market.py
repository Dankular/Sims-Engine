"""
world/job_market.py — Dynamic job vacancy posting and labour-matching.

Posts new vacancies based on current employment rate, matches unemployed sims
to openings by career performance, and maintains a wage index that reflects
per-career scarcity.

JobMarket.tick(engine) is called every tick from SimEngine.run_tick().
"""
from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from engine.engine import SimEngine

logger = logging.getLogger(__name__)

__all__ = ["JobVacancy", "JobMarket"]


@dataclass
class JobVacancy:
    vacancy_id: str
    career: str
    wage_bonus: float
    posted_tick: int
    filled: bool = False
    applicant_id: str = ""


class JobMarket:
    VACANCY_POST_INTERVAL: int = 15
    VACANCY_EXPIRE_TICKS: int = 45
    _FIRE_CHANCE: float = 0.30
    _FIRE_PERF_THRESHOLD: float = 25.0

    def __init__(self) -> None:
        self.vacancies: list[JobVacancy] = []
        self.wage_index: dict[str, float] = {}
        self._fill_rate_history: list[float] = []

    # ── Public tick ───────────────────────────────────────────────────────────

    def tick(self, engine: "SimEngine") -> None:
        try:
            self._expire_vacancies(engine)
            if engine.tick_count % self.VACANCY_POST_INTERVAL == 0:
                employment_rate = self._employment_rate(engine)
                self._post_vacancies(employment_rate, engine)
                self._match_unemployed(employment_rate, engine)
                self._maybe_fire(employment_rate, engine)
                self._update_wage_index(engine)
                self._record_fill_rate()
        except Exception as exc:
            logger.debug("[JobMarket] tick error: %s", exc)

    # ── Employment rate ───────────────────────────────────────────────────────

    def _employment_rate(self, engine: "SimEngine") -> float:
        sims = engine.sims
        if not sims:
            return 0.0
        employed = sum(
            1 for s in sims
            if s.profile.get("job", "Unemployed") not in ("Unemployed", "")
        )
        return employed / len(sims)

    # ── Vacancy posting ───────────────────────────────────────────────────────

    def _career_counts(self, engine: "SimEngine") -> dict[str, int]:
        counts: dict[str, int] = {}
        for sim in engine.sims:
            job = sim.profile.get("job", "")
            if job and job != "Unemployed":
                counts[job] = counts.get(job, 0) + 1
        return counts

    def _post_vacancies(
        self, employment_rate: float, engine: "SimEngine"
    ) -> None:
        if employment_rate < 0.6:
            return
        career_counts = self._career_counts(engine)
        if employment_rate > 0.85:
            eligible = [c for c, n in career_counts.items() if n >= 10]
            per_career = 2
            bonus = 1.25
        else:
            eligible = list(career_counts.keys())
            per_career = 1
            bonus = 1.0

        for career in eligible:
            for _ in range(per_career):
                v = JobVacancy(
                    vacancy_id=str(uuid.uuid4())[:8],
                    career=career,
                    wage_bonus=bonus,
                    posted_tick=engine.tick_count,
                )
                self.vacancies.append(v)

        logger.debug(
            "[JobMarket] posted %d vacancies (employment_rate=%.2f)",
            per_career * len(eligible),
            employment_rate,
        )

    # ── Matching ──────────────────────────────────────────────────────────────

    def _match_unemployed(
        self, employment_rate: float, engine: "SimEngine"
    ) -> None:
        unemployed = [
            s for s in engine.sims
            if s.profile.get("job", "Unemployed") in ("Unemployed", "")
        ]
        open_vacancies = [v for v in self.vacancies if not v.filled]
        if not unemployed or not open_vacancies:
            return

        # Sort candidates descending by career_performance
        unemployed.sort(key=lambda s: s.career_performance, reverse=True)
        # Group open vacancies by career
        by_career: dict[str, list[JobVacancy]] = {}
        for v in open_vacancies:
            by_career.setdefault(v.career, []).append(v)

        for sim in unemployed:
            if not by_career:
                break
            # Pick the first available vacancy (any career)
            career = next(iter(by_career))
            vacancy = by_career[career].pop(0)
            if not by_career[career]:
                del by_career[career]

            vacancy.filled = True
            vacancy.applicant_id = sim.sim_id
            sim.profile["job"] = vacancy.career

            engine._bus.emit(
                "hired",
                sim_id=sim.sim_id,
                name=sim.name,
                career=vacancy.career,
                wage_bonus=vacancy.wage_bonus,
                tick=engine.tick_count,
            )
            logger.debug(
                "[JobMarket] hired %s into %s (bonus=%.2f)",
                sim.name, vacancy.career, vacancy.wage_bonus,
            )
            if hasattr(sim, "moodlets"):
                sim.moodlets.add("just_promoted", source="job_market_hired")

    # ── Firing ────────────────────────────────────────────────────────────────

    def _maybe_fire(self, employment_rate: float, engine: "SimEngine") -> None:
        if employment_rate >= 0.6:
            return
        weak_employed = [
            s for s in engine.sims
            if s.profile.get("job", "Unemployed") not in ("Unemployed", "")
            and s.career_performance < self._FIRE_PERF_THRESHOLD
        ]
        if weak_employed and random.random() < self._FIRE_CHANCE:
            victim = random.choice(weak_employed)
            victim.profile["job"] = "Unemployed"
            engine._bus.emit(
                "laid_off",
                sim_id=victim.sim_id,
                name=victim.name,
                tick=engine.tick_count,
            )
            if hasattr(victim, "moodlets"):
                victim.moodlets.add("publicly_humiliated", source="job_market_fired")
            logger.debug("[JobMarket] laid off %s (oversupply)", victim.name)

    # ── Expiry ────────────────────────────────────────────────────────────────

    def _expire_vacancies(self, engine: "SimEngine") -> None:
        cutoff = engine.tick_count - self.VACANCY_EXPIRE_TICKS
        before = len(self.vacancies)
        self.vacancies = [
            v for v in self.vacancies
            if v.filled or v.posted_tick > cutoff
        ]
        expired = before - len(self.vacancies)
        if expired:
            logger.debug("[JobMarket] expired %d vacancies", expired)

    # ── Wage index ────────────────────────────────────────────────────────────

    def _update_wage_index(self, engine: "SimEngine") -> None:
        career_totals: dict[str, int] = {}
        for sim in engine.sims:
            job = sim.profile.get("job", "")
            if job and job != "Unemployed":
                career_totals[job] = career_totals.get(job, 0) + 1

        unfilled: dict[str, int] = {}
        for v in self.vacancies:
            if not v.filled:
                unfilled[v.career] = unfilled.get(v.career, 0) + 1

        self.wage_index = {
            career: 1.0 + (unfilled.get(career, 0) / max(1, total)) * 0.5
            for career, total in career_totals.items()
        }

    def _record_fill_rate(self) -> None:
        total = len(self.vacancies)
        filled = sum(1 for v in self.vacancies if v.filled)
        rate = filled / max(1, total)
        self._fill_rate_history.append(rate)
        self._fill_rate_history = self._fill_rate_history[-20:]

    # ── Public API ────────────────────────────────────────────────────────────

    def wage_multiplier_for(self, career: str) -> float:
        return self.wage_index.get(career, 1.0)

    def market_summary(self) -> dict:
        open_vacancies = [v for v in self.vacancies if not v.filled]
        by_career: dict[str, int] = {}
        for v in open_vacancies:
            by_career[v.career] = by_career.get(v.career, 0) + 1
        avg_wage = (
            round(sum(self.wage_index.values()) / len(self.wage_index), 4)
            if self.wage_index
            else 1.0
        )
        return {
            "open_vacancies":       len(open_vacancies),
            "by_career":            by_career,
            "avg_wage_multiplier":  avg_wage,
        }

    def summary(self) -> dict:
        return self.market_summary()
