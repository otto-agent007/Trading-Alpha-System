"""Metaculus API client — crowd probability lookups.

Used by the analyst as a fallback 'second opinion' anchor when
crowd_opinions.json (populated by the Linux box) has no entry for the
market being analysed.

No authentication required — the Metaculus community prediction API is public.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_BASE = "https://www.metaculus.com/api2"


class MetaculusClient:
    """Lightweight read-only Metaculus client.

    Only used for the `get_crowd_probability` fallback in the analyst.
    The Linux box is responsible for pre-populating crowd_opinions.json
    (via its data-ingestion track); this client fires only on cache misses.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def get_crowd_probability(self, question: str) -> float | None:
        """Search Metaculus for a matching open binary question.

        Returns the community median (q2) if a close match is found, else None.
        Uses the first 8 words of the question as search terms.
        """
        # Trim to first 8 words to keep the search query focused
        words = question.split()[:8]
        query = " ".join(words)

        try:
            resp = httpx.get(
                f"{_BASE}/questions/",
                params={
                    "search": query,
                    "type": "binary",
                    "status": "open",
                    "limit": 5,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.debug(f"MetaculusClient: search failed for '{query[:40]}': {e}")
            return None

        results = data.get("results", [])
        if not results:
            return None

        # Use the best-matching result's community prediction
        first = results[0]
        pred = first.get("community_prediction") or {}
        full = pred.get("full") or {}
        q2 = full.get("q2")

        if q2 is None:
            return None

        try:
            prob = float(q2)
            logger.debug(
                f"MetaculusClient: crowd prob {prob:.3f} for '{first.get('title', '')[:60]}'"
            )
            return prob
        except (ValueError, TypeError):
            return None

    def get_resolved_questions(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Fetch resolved binary questions for seeding episodic memory.

        Returns raw Metaculus question objects (compatible with seed_memory.parse_metaculus).
        """
        try:
            resp = httpx.get(
                f"{_BASE}/questions/",
                params={
                    "type": "binary",
                    "status": "resolved",
                    "limit": limit,
                    "offset": offset,
                    "order_by": "-resolve_time",
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
        except Exception as e:
            logger.warning(f"MetaculusClient: failed to fetch resolved questions: {e}")
            return []
