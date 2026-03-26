"""Test 5: Consolidation similarity matching."""
from __future__ import annotations

import pytest


def test_similar_patterns_match():
    """Same category + high word overlap → similar."""
    from core.memory.consolidation import _is_similar

    existing = {
        "category": "politics",
        "pattern": "Elections in swing states tend to be underpriced",
    }
    candidate = {
        "category": "politics",
        "pattern": "Swing state elections are systematically underpriced by the market",
    }
    assert _is_similar(candidate, existing) is True


def test_different_category_no_match():
    """Different category → not similar even with similar words."""
    from core.memory.consolidation import _is_similar

    existing = {
        "category": "crypto",
        "pattern": "BTC tends to overshoot on halving events",
    }
    candidate = {
        "category": "politics",
        "pattern": "BTC tends to overshoot on halving events",
    }
    assert _is_similar(candidate, existing) is False


def test_low_word_overlap_no_match():
    """Same category but unrelated content → not similar."""
    from core.memory.consolidation import _is_similar

    existing = {
        "category": "sports",
        "pattern": "Home field advantage underweighted in NFL markets",
    }
    candidate = {
        "category": "sports",
        "pattern": "Tennis players on clay have high win rates when seeded top 5",
    }
    assert _is_similar(candidate, existing) is False
