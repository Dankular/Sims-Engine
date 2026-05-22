"""
core/memory.py — Semantic episodic memory (System 1).

Two layers:
  _store      — per-pair short-term memories, each optionally carrying an
                SBERT embedding for cosine-similarity retrieval.
  _long_term  — per-sim consolidated long-term narratives written during sleep.

ChromaDB is used when SIM_V2_USE_CHROMA=1; otherwise pure in-memory numpy
cosine similarity is used as a zero-dependency fallback.
"""
from __future__ import annotations

import uuid
import os

from config import MAX_MEMORIES, MEMORY_THRESHOLD

try:
    import chromadb as _chromadb
    CHROMA_OK = True
except ImportError:
    _chromadb = None
    CHROMA_OK = False

try:
    from sentence_transformers import SentenceTransformer as _SentenceTransformer
    SBERT_OK = True
except ImportError:
    _SentenceTransformer = None
    SBERT_OK = False

_sbert_model = None
_LONG_TERM_MAX = 30


def _get_sbert():
    global _sbert_model
    if _sbert_model is None and SBERT_OK:
        from config import HF_SENTENCE_MODEL, HF_SENTENCE_MODEL_FULL
        for model_id in (HF_SENTENCE_MODEL, HF_SENTENCE_MODEL_FULL,
                         "sentence-transformers/all-MiniLM-L6-v2"):
            try:
                _sbert_model = _SentenceTransformer(model_id, local_files_only=True)
                break
            except Exception:
                continue
    return _sbert_model


def _cosine_top_k(query_vec, memories: list[dict], top_k: int) -> list[dict]:
    """Return top-k memories by cosine similarity. Pure numpy, no external deps."""
    try:
        import numpy as np
        q = np.array(query_vec, dtype=float)
        q_norm = np.linalg.norm(q) + 1e-9
        scored: list[tuple[float, dict]] = []
        for m in memories:
            emb = m.get("embedding")
            if emb is None:
                continue
            e = np.array(emb, dtype=float)
            score = float(np.dot(q, e) / (q_norm * (np.linalg.norm(e) + 1e-9)))
            scored.append((score, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [m for _, m in scored[:top_k]]
    except Exception:
        return memories[-top_k:]


def _cross_encoder_rerank(query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """
    System 6 — Cross-encoder memory reranking.
    Re-scores (query, memory_text) pairs with ms-marco-MiniLM for higher
    contextual precision than bi-encoder cosine alone.
    """
    try:
        from llm.small_models import get_cross_encoder
        ce = get_cross_encoder()
        if ce is None or not candidates:
            return candidates[:top_k]
        pairs = [(query, m.get("text", "")[:256]) for m in candidates]
        scores = ce.predict(pairs, show_progress_bar=False)
        ranked = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        return [m for _, m in ranked[:top_k]]
    except Exception:
        return candidates[:top_k]


def _embed(text: str) -> list[float] | None:
    model = _get_sbert()
    if model is None:
        return None
    try:
        return model.encode([text], show_progress_bar=False)[0].tolist()
    except Exception:
        return None


class _LocalEmbeddingFunction:
    def __init__(self, model):
        self._model = model

    def __call__(self, input):
        return self._model.encode(list(input), show_progress_bar=False).tolist()


class MemoryStore:
    def __init__(self):
        # per-pair short-term: {pair_key: [{id, text, valence, tick, embedding}]}
        self._store: dict[str, list[dict]] = {}
        # per-sim long-term consolidated narratives
        self._long_term: dict[str, list[dict]] = {}
        self._chroma = None
        enable_chroma = os.environ.get("SIM_V2_USE_CHROMA", "0") == "1"
        if CHROMA_OK and enable_chroma and _chromadb is not None:
            try:
                client = _chromadb.Client()
                embedder = None
                model = _get_sbert()
                if model is not None:
                    embedder = _LocalEmbeddingFunction(model)
                self._chroma = client.get_or_create_collection(
                    "sim_memories", embedding_function=embedder
                )
            except Exception:
                self._chroma = None

    def write(
        self,
        sim_a: str,
        sim_b: str,
        memory_tag: str,
        valence: float,
        interaction_id: str = "",
        tick: int = 0,
    ) -> None:
        key = f"{min(sim_a, sim_b)}_{max(sim_a, sim_b)}"
        if abs(valence) < MEMORY_THRESHOLD:
            return
        iid = interaction_id or uuid.uuid4().hex[:8]

        embedding = _embed(memory_tag)

        if self._chroma:
            try:
                self._chroma.add(
                    documents=[memory_tag],
                    metadatas=[{
                        "pair": key,
                        "valence": valence,
                        "sim_a": sim_a,
                        "sim_b": sim_b,
                        "interaction_id": iid,
                        "tick": tick,
                    }],
                    ids=[iid],
                )
            except Exception:
                pass
            # Also keep in _store so consolidation can scan it
            self._store.setdefault(key, []).append(
                {"id": iid, "text": memory_tag, "valence": valence,
                 "tick": tick, "embedding": embedding}
            )
            self._store[key] = self._store[key][-MAX_MEMORIES:]
            return

        entry = {
            "id": iid,
            "text": memory_tag,
            "valence": valence,
            "tick": tick,
            "embedding": embedding,
        }
        self._store.setdefault(key, []).append(entry)
        self._store[key] = self._store[key][-MAX_MEMORIES:]

    def recall(self, sim_a: str, sim_b: str, query: str = "", n: int = 3) -> str:
        """
        Return a formatted memory string for LLM prompts.
        When a query string is provided and SBERT is loaded, returns the
        semantically most relevant memories rather than the most recent.
        """
        key = f"{min(sim_a, sim_b)}_{max(sim_a, sim_b)}"

        if self._chroma:
            try:
                q_text = query if query else f"{sim_a} and {sim_b}"
                results = self._chroma.query(
                    query_texts=[q_text],
                    n_results=n,
                    where={"pair": key},
                )
                docs_list = results.get("documents", [[]])
                docs = docs_list[0] if (docs_list and isinstance(docs_list[0], list)) else []
                return "; ".join(docs) if docs else "none"
            except Exception:
                pass

        memories = self._store.get(key, [])
        if not memories:
            return "none"

        if query:
            q_emb = _embed(query)
            if q_emb is not None:
                top = _cosine_top_k(q_emb, memories, n)
                if top:
                    return "; ".join(
                        f"[{m['id']}] {m['text']} (valence={m['valence']:+.2f})"
                        for m in top
                    )

        recent = memories[-n:]
        return "; ".join(
            f"[{m['id']}] {m['text']} (valence={m['valence']:+.2f})"
            for m in recent
        )

    def retrieve_relevant(
        self,
        sim_a: str,
        sim_b: str,
        query: str,
        top_k: int = 3,
    ) -> list[dict]:
        """
        Return the top-k most contextually relevant memory dicts for a pair.
        Pipeline: SBERT bi-encoder retrieves top-20 candidates →
        cross-encoder (ms-marco-MiniLM) reranks → return top-k.
        """
        key = f"{min(sim_a, sim_b)}_{max(sim_a, sim_b)}"
        memories = self._store.get(key, [])
        if not memories:
            return []

        # Stage 1: fast bi-encoder retrieval (top-20 candidates)
        q_emb = _embed(query)
        if q_emb is None:
            candidates = memories[-20:]
        else:
            candidates = _cosine_top_k(q_emb, memories, min(20, len(memories)))

        if len(candidates) <= top_k:
            return candidates

        # Stage 2: cross-encoder reranking for precision
        return _cross_encoder_rerank(query, candidates, top_k)

    def write_long_term(
        self,
        sim_id: str,
        text: str,
        tag: str,
        valence: float,
        tick: int,
    ) -> None:
        """Store a consolidated long-term memory for a single sim."""
        entry = {
            "text": text,
            "tag": tag,
            "valence": valence,
            "tick": tick,
            "embedding": _embed(text),
        }
        self._long_term.setdefault(sim_id, []).append(entry)
        self._long_term[sim_id] = self._long_term[sim_id][-_LONG_TERM_MAX:]

    def recall_long_term(self, sim_id: str, query: str = "", n: int = 2) -> str:
        """Return formatted long-term memories for a single sim."""
        entries = self._long_term.get(sim_id, [])
        if not entries:
            return ""
        if query:
            q_emb = _embed(query)
            if q_emb is not None:
                top = _cosine_top_k(q_emb, entries, n)
                if top:
                    return "; ".join(e["text"][:120] for e in top)
        return "; ".join(e["text"][:120] for e in entries[-n:])


    # ── Salience resurfacing ──────────────────────────────────────────────────

    def salience_score(self, entry: dict, current_tick: int,
                       trigger_context: dict | None = None) -> float:
        """
        Score a memory for retrieval priority.

        Drivers:
          • recency   — decays exponentially with tick gap
          • valence   — high positive or negative valence scores higher
          • witness   — shared memories with a witness present score +0.2
          • anniversary — if current_tick % created_tick_mod ≈ 0
          • location  — if current_lot_id matches memory lot
          • repetition — same tag seen many times → higher salience
        """
        tick_gap   = max(1, current_tick - entry.get("tick", 0))
        recency    = 1.0 / (1 + tick_gap / 50.0)
        valence    = abs(entry.get("valence", 0.0)) * 0.4
        base       = recency * 0.5 + valence

        if trigger_context:
            # Anniversary: memory tick is a multiple of 50 ticks ago
            created = entry.get("tick", 0)
            if created > 0 and (current_tick - created) % 50 < 3:
                base += 0.25
            # Location match
            if (entry.get("lot_id")
                    and entry.get("lot_id") == trigger_context.get("lot_id")):
                base += 0.20
            # Witness present in current interaction
            if (entry.get("sim_b")
                    and entry.get("sim_b") == trigger_context.get("partner_id")):
                base += 0.30

        return min(1.0, base)

    def recall_salient(
        self,
        sim_a_id: str,
        sim_b_id: str = "",
        trigger_context: dict | None = None,
        current_tick: int = 0,
        n: int = 3,
    ) -> str:
        """
        Retrieve the most salient memories for sim_a, with optional bias
        toward memories triggered by location, anniversary, or witness.

        Returns a formatted string ready for adjudicator injection.
        """
        # Collect all pair memories for sim_a
        candidates: list[dict] = []
        for key, entries in self._store.items():
            if sim_a_id in key:
                for entry in entries:
                    scored = dict(entry)
                    scored["_salience"] = self.salience_score(
                        entry, current_tick, trigger_context
                    )
                    candidates.append(scored)

        if not candidates:
            return ""

        top = sorted(candidates, key=lambda e: -e["_salience"])[:n]
        parts: list[str] = []
        for e in top:
            sal = e.get("_salience", 0.0)
            if sal < 0.2:
                continue
            parts.append(f"{e.get('tag','?')} (valence={e.get('valence',0):.1f})")

        return "; ".join(parts) if parts else ""
