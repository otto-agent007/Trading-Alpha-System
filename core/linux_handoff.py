"""Linux research factory handoff.

The always-on Linux box runs research tracks continuously and writes findings
to DATA_PATH/linux_shared/.  This module provides a LinuxData singleton that
reads those files on demand so the Windows trading system can act on them.

Files are read fresh on every call — no caching — so the trading system
always sees the latest research output without a restart.
"""
from __future__ import annotations

import json
import logging

from config import DATA_PATH

logger = logging.getLogger(__name__)

_SHARED_DIR = DATA_PATH / "linux_shared"


class LinuxData:
    """Read-only access to research factory output files.

    All methods return safe defaults if the file doesn't exist or is malformed,
    so the trading system degrades gracefully when the Linux box is offline.
    """

    def _load(self, filename: str) -> dict | None:
        path = _SHARED_DIR / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"LinuxData: failed to read {filename}: {e}")
            return None

    # ── Calibration ──────────────────────────────────────────────────

    def get_calibration(self) -> dict:
        """Get calibration corrections from Track 1."""
        data = self._load("calibration.json")
        return data or {}

    # ── Crowd opinions ───────────────────────────────────────────────

    def get_crowd_opinions(self) -> dict:
        """Get crowd probability estimates keyed by market_id.

        Used by analyst as a 'second opinion' anchor from Metaculus/Manifold.
        """
        data = self._load("crowd_opinions.json")
        return data.get("opinions", {}) if data else {}

    # ── Arbitrage alerts ─────────────────────────────────────────────

    def get_arbitrage_alerts(self) -> list[dict]:
        """Get active cross-platform arbitrage opportunities from price monitor."""
        data = self._load("arbitrage_alerts.json")
        return data.get("alerts", []) if data else []

    # ── Fast alerts ───────────────────────────────────────────────────

    def get_fast_alerts(self) -> list[dict]:
        """Get time-sensitive analysis triggers from news sentinel."""
        data = self._load("fast_alerts.json")
        return data.get("alerts", []) if data else []

    # ── Statistical patterns ──────────────────────────────────────────

    def get_statistical_edges(self, category: str = None) -> list[dict]:
        """Get exploitable statistical patterns from Track 2.

        Returns patterns sorted by edge size. Optionally filter by category.
        """
        data = self._load("stat_patterns_findings.json")
        if not data:
            return []
        patterns = data.get("patterns", [])
        if category:
            patterns = [p for p in patterns
                        if p.get("hypothesis", {}).get("category") in (category, "_all")]
        return sorted(patterns, key=lambda p: -abs(p.get("edge", 0)))[:20]

    # ── Entry timing ──────────────────────────────────────────────────

    def get_optimal_entry_timing(self, category: str) -> float | None:
        """Get the optimal entry point (as fraction of market lifetime) for a category.

        Returns e.g. 0.33 meaning "enter at 33% through the market's life."
        Returns None if no data available.
        """
        data = self._load("entry_timing_findings.json")
        if not data:
            return None
        best = data.get("best_timing_per_category", {})
        entry = best.get(category) or best.get("_all")
        if entry:
            return entry.get("best_entry_pct")
        return None

    # ── Scanner filters ───────────────────────────────────────────────

    def get_optimal_scanner_filters(self) -> dict | None:
        """Get the best scanner filter configuration from Track 5.

        Returns dict with min_vol, price_floor, price_ceiling.
        """
        data = self._load("scanner_filters_findings.json")
        if not data:
            return None
        top = data.get("top_filter_combos", [])
        if top:
            return top[0].get("config")
        return None

    # ── Portfolio allocation ──────────────────────────────────────────

    def get_category_allocation(self) -> dict:
        """Get recommended bankroll allocation per category.

        Returns dict like {"crypto": 0.35, "politics": 0.25, ...}
        """
        data = self._load("portfolio_optimizer_findings.json")
        if not data:
            return {}
        return data.get("allocation", {}).get("allocation", {})

    # ── Best analyst prompt ───────────────────────────────────────────

    def get_best_analyst_prompt(self) -> str | None:
        """Get the winning analyst system prompt from Track 3.

        Returns the full prompt text, or None if not available.
        """
        data = self._load("prompt_optimizer_findings.json")
        if not data:
            return None
        return data.get("best_prompt_text")

    # ── Human feedback ────────────────────────────────────────────────

    def get_human_feedback(self) -> dict:
        """Get human directives from Obsidian Feedback.md.

        Returns structured feedback with skip_categories, boost_categories,
        confidence_adjustments, param_overrides, analyst_notes, etc.
        """
        data = self._load("human_feedback.json")
        return data or {
            "skip_categories": [],
            "boost_categories": [],
            "confidence_adjustments": {},
            "param_overrides": {},
            "analyst_notes": [],
            "force_analyze": [],
            "skip_markets": [],
        }


# Module-level singleton — import LINUX instead of instantiating LinuxData directly.
LINUX = LinuxData()
