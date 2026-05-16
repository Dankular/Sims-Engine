from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Household:
    id: str
    name: str
    member_ids: list[str]
    funds: float = 0.0
    home_venue: dict | None = None

    def __post_init__(self) -> None:
        if self.home_venue is None:
            self.home_venue = {
                "name": "home (1:1)",
                "noise": 0.1,
                "intimacy": 0.9,
                "crowd": 0.05,
            }


def assign_households(sims: list["Sim"]) -> list[Household]:
    households: list[Household] = []
    household_id = 0
    for index in range(0, len(sims), 2):
        members = sims[index : index + 2]
        household = Household(
            id=f"hh_{household_id}",
            name=f"{members[0].name.split()[0]} household",
            member_ids=[sim.sim_id for sim in members],
        )
        households.append(household)
        for sim in members:
            sim.household_id = household.id
        household_id += 1
    return households


__all__ = ["Household", "assign_households"]
