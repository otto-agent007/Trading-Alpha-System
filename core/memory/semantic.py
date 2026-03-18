from __future__ import annotations

import logging
from datetime import datetime, timezone

import chromadb

from config import GOOGLE_API_KEY, MEMORY_PATH
from core.memory.embeddings import get_gemini_embedder, get_local_embedder

logger = logging.getLogger(__name__)


class SemanticMemory:
    """Stores learned patterns and rules in ChromaDB.

    Uses Gemini Embedding when GOOGLE_API_KEY is set (higher quality).
    Falls back to the shared MiniLM embedder (see embeddings.py).
    """

    def __init__(self) -> None:
        MEMORY_PATH.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(MEMORY_PATH / "chroma"))
        self._collection = self._client.get_or_create_collection("learnings")

        if GOOGLE_API_KEY:
            gemini = get_gemini_embedder(GOOGLE_API_KEY)
            if gemini is not None:
                self._embedder = gemini
            else:
                self._embedder = get_local_embedder()
        else:
            logger.info("SemanticMemory: GOOGLE_API_KEY not set, using shared MiniLM")
            self._embedder = get_local_embedder()  # shared singleton

    def _encode(self, text: str) -> list[float]:
        """Encode text, normalizing output to list[float]."""
        emb = self._embedder.encode(text)
        if not isinstance(emb, list):
            emb = emb.tolist()
        return emb

    def store_learning(self, learning: dict) -> None:
        """Store a learned pattern.

        Expected keys: category, pattern, confidence, evidence_count.
        """
        learning_id = learning.get("id") or f"learn_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        text = f"{learning.get('category', '')}: {learning.get('pattern', '')}"
        emb = self._encode(text)

        meta = {
            "category": str(learning.get("category", "")),
            "pattern": str(learning.get("pattern", "")),
            "confidence": float(learning.get("confidence", 0.5)),
            "evidence_count": int(learning.get("evidence_count", 1)),
            "created": datetime.now(timezone.utc).isoformat(),
            "updated": datetime.now(timezone.utc).isoformat(),
        }

        self._collection.upsert(
            ids=[learning_id],
            documents=[text],
            embeddings=[emb],
            metadatas=[meta],
        )
        logger.info(f"SemanticMemory: stored learning {learning_id} (confidence={meta['confidence']:.2f})")

    def query_patterns(self, context: str, n: int = 5) -> list[dict]:
        """Find learned patterns relevant to a context string.

        Returns dicts with an ``_id`` key so callers can reference them
        for updates (e.g. consolidation.py -> update_confidence).
        """
        count = self._collection.count()
        if count == 0:
            return []
        emb = self._encode(context)
        results = self._collection.query(
            query_embeddings=[emb],
            n_results=min(n, count),
            include=["metadatas", "documents"],
        )
        metas = results.get("metadatas", [[]])[0]
        ids = results.get("ids", [[]])[0]
        # Attach the ChromaDB document ID so callers can update_confidence()
        for meta, doc_id in zip(metas, ids):
            meta["_id"] = doc_id
        return metas

    def update_confidence(self, learning_id: str, correct: bool) -> None:
        """Bayesian-style confidence update based on new evidence.

        Correct prediction -> confidence moves toward 1.0
        Incorrect prediction -> confidence moves toward 0.0
        """
        try:
            result = self._collection.get(ids=[learning_id], include=["metadatas"])
            if not result["metadatas"]:
                return
            meta = result["metadatas"][0]
            old_conf = float(meta.get("confidence", 0.5))
            evidence = int(meta.get("evidence_count", 1))

            # Weighted update: more evidence = slower change
            weight = 1.0 / (evidence + 1)
            if correct:
                new_conf = old_conf + weight * (1.0 - old_conf)
            else:
                new_conf = old_conf - weight * old_conf
            new_conf = max(0.0, min(1.0, new_conf))

            meta["confidence"] = new_conf
            meta["evidence_count"] = evidence + 1
            meta["updated"] = datetime.now(timezone.utc).isoformat()

            self._collection.update(ids=[learning_id], metadatas=[meta])
            logger.info(
                f"SemanticMemory: updated {learning_id} confidence "
                f"{old_conf:.2f} -> {new_conf:.2f} (evidence={evidence + 1})"
            )
        except Exception as e:
            logger.error(f"SemanticMemory: confidence update failed for {learning_id}: {e}")

    def prune(self, min_confidence: float = 0.3, min_evidence: int = 10) -> int:
        """Remove patterns with low confidence after enough evidence."""
        results = self._collection.get(include=["metadatas"])
        ids_to_delete: list[str] = []
        all_ids = results.get("ids", [])
        all_metas = results.get("metadatas", [])

        for doc_id, meta in zip(all_ids, all_metas):
            evidence = int(meta.get("evidence_count", 0))
            confidence = float(meta.get("confidence", 1.0))
            if evidence >= min_evidence and confidence < min_confidence:
                ids_to_delete.append(doc_id)

        if ids_to_delete:
            self._collection.delete(ids=ids_to_delete)
            logger.info(f"SemanticMemory: pruned {len(ids_to_delete)} low-confidence patterns")
        return len(ids_to_delete)

    def get_all(self) -> list[dict]:
        """Return all learned patterns."""
        results = self._collection.get(include=["metadatas", "documents"])
        return results.get("metadatas", [])

    def count(self) -> int:
        return self._collection.count()
