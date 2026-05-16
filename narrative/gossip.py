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
