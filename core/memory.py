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
        """Return the top-k most semantically relevant memory dicts for a pair."""
        key = f"{min(sim_a, sim_b)}_{max(sim_a, sim_b)}"
        memories = self._store.get(key, [])
        if not memories:
            return []
        q_emb = _embed(query)
        if q_emb is None:
            return memories[-top_k:]
        return _cosine_top_k(q_emb, memories, top_k)

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
