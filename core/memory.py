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


def _get_sbert():
    global _sbert_model
    if _sbert_model is None and SBERT_OK:
        try:
            _sbert_model = _SentenceTransformer(
                "sentence-transformers/all-MiniLM-L6-v2", local_files_only=True
            )
        except Exception:
            _sbert_model = None
    return _sbert_model


class _LocalEmbeddingFunction:
    """Wraps sentence-transformers to satisfy ChromaDB's EmbeddingFunction protocol."""

    def __init__(self, model):
        self._model = model

    def __call__(self, input):
        vectors = self._model.encode(list(input), show_progress_bar=False)
        return vectors.tolist()


class MemoryStore:
    def __init__(self):
        self._store: dict[str, list[str]] = {}
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
    ) -> None:
        key = f"{min(sim_a, sim_b)}_{max(sim_a, sim_b)}"
        if abs(valence) < MEMORY_THRESHOLD:
            return
        iid = interaction_id or uuid.uuid4().hex[:8]
        if self._chroma:
            try:
                self._chroma.add(
                    documents=[memory_tag],
                    metadatas=[
                        {
                            "pair": key,
                            "valence": valence,
                            "sim_a": sim_a,
                            "sim_b": sim_b,
                            "interaction_id": iid,
                        }
                    ],
                    ids=[iid],
                )
                return
            except Exception:
                pass
        self._store.setdefault(key, []).append(
            f"[{iid}] {memory_tag} (valence={valence:+.2f})"
        )
        self._store[key] = self._store[key][-MAX_MEMORIES:]

    def recall(self, sim_a: str, sim_b: str, n: int = 3) -> str:
        key = f"{min(sim_a, sim_b)}_{max(sim_a, sim_b)}"
        if self._chroma:
            try:
                results = self._chroma.query(
                    query_texts=[f"{sim_a} and {sim_b}"],
                    n_results=n,
                    where={"pair": key},
                )
                docs_list = results.get("documents", [[]])
                docs = []
                if isinstance(docs_list, list) and docs_list:
                    first = docs_list[0]
                    if isinstance(first, list):
                        docs = first
                return "; ".join(docs) if docs else "none"
            except Exception:
                pass
        memories = self._store.get(key, [])
        return "; ".join(memories[-n:]) if memories else "none"
