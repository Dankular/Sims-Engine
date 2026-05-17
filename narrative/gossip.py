from config import MAX_GOSSIP_FACTS


class GossipGraph:
    def __init__(self):
        self._facts: dict[tuple[str, str], list[str]] = {}

    def learn(self, knower_id: str, subject_id: str, fact: str) -> None:
        key = (knower_id, subject_id)
        facts = self._facts.setdefault(key, [])
        if fact not in facts:
            facts.append(fact)
            if len(facts) > MAX_GOSSIP_FACTS:
                facts.pop(0)

    def recall(self, knower_id: str, subject_id: str, n: int = 3) -> str:
        facts = self._facts.get((knower_id, subject_id), [])
        return "; ".join(facts[-n:]) if facts else ""

    def spread(self, from_id: str, to_id: str, about_id: str) -> None:
        for fact in self._facts.get((from_id, about_id), [])[-2:]:
            self.learn(to_id, about_id, fact)

    def spread_trait_gossip(self, from_sim, to_sim, about_sim) -> list[str]:
        knowledge = getattr(from_sim, "trait_knowledge", {}).get(about_sim.sim_id, {})
        known_traits = list(knowledge.get("known_traits", []))
        if not known_traits:
            return []
        forwarded = known_traits[:2]
        to_knowledge = getattr(to_sim, "trait_knowledge", {})
        payload = to_knowledge.setdefault(
            about_sim.sim_id,
            {"known_traits": [], "suspected_traits": {}, "confidence": {}},
        )
        current = set(payload.get("known_traits", []))
        for trait in forwarded:
            current.add(trait)
            payload["confidence"][trait] = max(
                0.5, float(payload["confidence"].get(trait, 0.0))
            )
            self.learn(to_sim.sim_id, about_sim.sim_id, f"trait:{trait}")
        payload["known_traits"] = sorted(current)
        to_sim.trait_knowledge = to_knowledge
        return forwarded
