from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import chromadb

from config import MEMORY_PATH
from core.memory.embeddings import get_local_embedder

logger = logging.getLogger(__name__)


class EpisodicMemory:
    """Stores every decision, analysis, and outcome in ChromaDB.

    Uses the shared MiniLM embedder (see embeddings.py) — no duplicate model load.
    """

    def __init__(self) -> None:
        MEMORY_PATH.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(MEMORY_PATH / "chroma"))
        self._collection = self._client.get_or_create_collection("episodes")
        self._embedder = get_local_embedder()  # shared singleton

    def record(self, episode: dict) -> None:
        """Store an episode.

        Expected keys: market_id, platform, action, reasoning, category.
        Optional keys: outcome, pnl, confidence, edge, question.
        """
        episode_id = (
            episode.get("id")
            or episode.get("market_id", "")
            + "_"
            + datetime.now().strftime("%Y%m%d_%H%M%S")
        )
        text = json.dumps(episode, default=str)
        emb = self._embedder.encode(text).tolist()

        # Flatten metadata to ChromaDB-safe primitives
        meta: dict = {"timestamp": datetime.now(timezone.utc).isoformat()}
        for k, v in episode.items():
            if isinstance(v, (str, int, float, bool)):
                meta[k] = v
            elif v is None:
                meta[k] = ""
            else:
                meta[k] = str(v)

        self._collection.upsert(
            ids=[str(episode_id)],
            documents=[text],
            embeddings=[emb],
            metadatas=[meta],
        )
        logger.info(f"Episodic memory: recorded {episode_id}")

    def recall(self, query: str, n: int = 5) -> list[dict]:
        """Semantic search over episodes. Returns list of metadata dicts."""
        count = self._collection.count()
        if count == 0:
            return []
        emb = self._embedder.encode(query).tolist()
        results = self._collection.query(
            query_embeddings=[emb],
            n_results=min(n, count),
            include=["metadatas", "documents"],
        )
        metas = results.get("metadatas", [[]])[0]
        docs = results.get("documents", [[]])[0]
        for meta, doc in zip(metas, docs):
            meta["_document"] = doc
        return metas

    def get_recent(self, hours: int = 24) -> list[dict]:
        """Return all episodes from the last N hours."""
        count = self._collection.count()
        if count == 0:
            return []

        results = self._collection.get(include=["metadatas", "documents"])
        metas = results.get("metadatas") or []
        docs = results.get("documents") or []

        cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
        recent: list[dict] = []
        for meta, doc in zip(metas, docs):
            ts_str = meta.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
            except Exception:
                ts = 0
            if ts >= cutoff:
                meta["_document"] = doc
                recent.append(meta)
        return recent

    def count(self) -> int:
        return self._collection.count()
