"""Shared embedding singletons.

Both EpisodicMemory and SemanticMemory need an embedder.  Loading
``all-MiniLM-L6-v2`` twice wastes ~500 MB of RAM.  This module
provides a single shared instance via ``get_local_embedder()``.
"""
from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

# Type alias — both SentenceTransformer and GeminiEmbedder expose .encode()
Embedder = object  # duck-typed: must have encode(str) -> list[float] | ndarray


@lru_cache(maxsize=1)
def get_local_embedder() -> Embedder:
    """Return a shared SentenceTransformer('all-MiniLM-L6-v2') instance.

    Called at most once per process; subsequent calls return the cached object.
    """
    from sentence_transformers import SentenceTransformer

    logger.info("Loading shared MiniLM embedder (one-time)")
    return SentenceTransformer("all-MiniLM-L6-v2")


def get_gemini_embedder(api_key: str) -> Embedder | None:
    """Try to build a Gemini embedder.  Returns None on failure."""
    try:
        from google import genai
        from google.genai import types

        class _GeminiEmbedder:
            def __init__(self, key: str) -> None:
                self._client = genai.Client(api_key=key)
                self._model = "models/text-embedding-004"

            def encode(self, text: str) -> list[float]:
                result = self._client.models.embed_content(
                    model=self._model,
                    contents=text,
                    config=types.EmbedContentConfig(
                        task_type="SEMANTIC_SIMILARITY"
                    ),
                )
                return result.embeddings[0].values

        embedder = _GeminiEmbedder(api_key)
        logger.info("Gemini Embedding initialized")
        return embedder
    except Exception as e:
        logger.warning(f"Gemini Embedding init failed ({e}), will use local MiniLM")
        return None
