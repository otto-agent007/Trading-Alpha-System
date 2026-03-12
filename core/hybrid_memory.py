import chromadb
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from sentence_transformers import SentenceTransformer
from config import MEMORY_PATH, OBSIDIAN_VAULT

logger = logging.getLogger(__name__)


class HybridMemory:
    def __init__(self):
        self.client = chromadb.PersistentClient(path=str(MEMORY_PATH / "chroma"))
        self.collection = self.client.get_or_create_collection("alpha_episodes")
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")

    def add_episode(self, episode: dict) -> None:
        """
        Store a prediction market backtest episode in ChromaDB.

        Required keys: id, category, position, accepted, expected_value, win_rate
        """
        text = json.dumps(episode, default=str)
        emb = self.embedder.encode(text).tolist()

        # Surprise metric: large EV (good or bad) and large deviation from 50% win rate
        surprise = abs(episode.get("expected_value", 0.0)) + abs(
            episode.get("win_rate", 0.5) - 0.5
        )

        # ChromaDB metadata must be flat primitives
        metadata = {
            "timestamp": datetime.now().isoformat(),
            "surprise": float(surprise),
            "category": str(episode.get("category", "")),
            "position": str(episode.get("position", "")),
            "accepted": bool(episode.get("accepted", False)),
            "expected_value": float(episode.get("expected_value", 0.0)),
            "win_rate": float(episode.get("win_rate", 0.0)),
            "sample_size": int(episode.get("sample_size", 0)),
            "kelly_fraction": float(episode.get("kelly_fraction", 0.0)),
        }

        self.collection.add(
            documents=[text],
            embeddings=[emb],
            metadatas=[metadata],
            ids=[str(episode["id"])],
        )
        logger.info(f"Memory: stored episode {episode['id']} (surprise={surprise:.4f})")

    def query(self, text: str, n: int = 5) -> list[dict]:
        """Semantic search over stored episodes. Returns list of metadata dicts."""
        count = self.collection.count()
        if count == 0:
            return []
        emb = self.embedder.encode(text).tolist()
        results = self.collection.query(
            query_embeddings=[emb],
            n_results=min(n, count),
            include=["metadatas", "documents"],
        )
        return results.get("metadatas", [[]])[0]

    def consolidate(self) -> None:
        """
        Nightly consolidation: summarise past episodes by category using the LLM
        and write a Weekly Summary to the Obsidian vault.
        """
        logger.info("HybridMemory: nightly consolidation starting...")
        try:
            results = self.collection.get(include=["documents", "metadatas"])
            docs = results.get("documents") or []
            metas = results.get("metadatas") or []

            if not docs:
                logger.info("HybridMemory: no episodes to consolidate.")
                return

            # Group episode text snippets by category
            by_category: dict[str, list[dict]] = defaultdict(list)
            for doc, meta in zip(docs, metas):
                cat = meta.get("category", "unknown")
                by_category[cat].append({
                    "accepted": meta.get("accepted"),
                    "expected_value": meta.get("expected_value"),
                    "win_rate": meta.get("win_rate"),
                    "position": meta.get("position"),
                    "snippet": doc[:400],
                })

            from core.model_router import ModelRouter
            router = ModelRouter()

            summary_lines = [
                f"# Weekly Consolidation — {datetime.now().strftime('%Y-%m-%d')}\n",
                f"Total episodes stored: {len(docs)}\n",
            ]

            for cat, episodes in sorted(by_category.items()):
                prompt = (
                    f"You are a prediction market analyst. "
                    f"Below are backtest results for category '{cat}':\n"
                    f"{json.dumps(episodes[:10], default=str)}\n\n"
                    f"Return JSON: "
                    f'{{\"summary\": \"2-3 sentence analysis of what conditions showed edge vs. no edge and why\"}}'
                )
                try:
                    raw = router.chat(
                        [
                            {"role": "system", "content": "You are a concise prediction market analyst. Output only JSON."},
                            {"role": "user", "content": prompt},
                        ],
                        temperature=0.2,
                    )
                    data = json.loads(raw)
                    text_summary = data.get("summary", raw)
                except Exception as e:
                    text_summary = f"(Consolidation failed: {e})"

                accepted_count = sum(1 for ep in episodes if ep.get("accepted"))
                summary_lines.append(
                    f"## {cat.capitalize()} ({len(episodes)} episodes, {accepted_count} accepted)\n"
                    f"{text_summary}\n"
                )

            vault_path = (
                OBSIDIAN_VAULT / "Alpha Research" / "Dashboard" / "Weekly_Summary.md"
            )
            vault_path.parent.mkdir(parents=True, exist_ok=True)
            vault_path.write_text("\n".join(summary_lines))
            logger.info(f"HybridMemory: consolidation written to {vault_path}")

        except Exception as e:
            logger.error(f"HybridMemory: consolidation error: {e}")


memory = HybridMemory()
