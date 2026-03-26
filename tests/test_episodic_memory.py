"""Test 7: EpisodicMemory record + recall round-trip with isolated ChromaDB."""
from __future__ import annotations

import pytest


def test_record_and_recall(tmp_path, monkeypatch):
    """record() then recall() should return the recorded episode."""
    import config
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "memory")
    (tmp_path / "memory").mkdir()

    from core.memory.episodic import EpisodicMemory

    # Fresh instance pointing at the tmp path
    em = EpisodicMemory()

    episode = {
        "market_id": "test_market_001",
        "platform": "polymarket",
        "question": "Will BTC hit 200k by end of 2026?",
        "category": "crypto",
        "action": "buy_yes",
        "outcome": "Yes",
        "edge": 0.12,
        "confidence": 0.65,
    }
    em.record(episode)

    results = em.recall("BTC cryptocurrency prediction", n=3)
    assert len(results) >= 1

    # The stored episode should be among the results
    found = any(r.get("market_id") == "test_market_001" for r in results)
    assert found, f"Episode not found in recall results: {results}"


def test_count_increases_after_record(tmp_path, monkeypatch):
    """count() should reflect the number of stored episodes."""
    import config
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "memory")
    (tmp_path / "memory").mkdir()

    from core.memory.episodic import EpisodicMemory
    em = EpisodicMemory()

    before = em.count()

    em.record({"market_id": "ep1", "platform": "kalshi", "question": "Test?", "outcome": "Yes"})
    em.record({"market_id": "ep2", "platform": "polymarket", "question": "Test2?", "outcome": "No"})

    assert em.count() == before + 2


def test_get_recent_returns_new_episodes(tmp_path, monkeypatch):
    """get_recent() should include episodes just recorded."""
    import config
    monkeypatch.setattr(config, "MEMORY_PATH", tmp_path / "memory")
    (tmp_path / "memory").mkdir()

    from core.memory.episodic import EpisodicMemory
    em = EpisodicMemory()

    em.record({
        "market_id": "recent_ep",
        "platform": "polymarket",
        "question": "Recent market question?",
        "outcome": "Yes",
    })

    recent = em.get_recent(hours=1)
    # Episodes just recorded within the last hour should appear
    found = any(r.get("market_id") == "recent_ep" for r in recent)
    assert found, f"Recently recorded episode not found in get_recent(): {[r.get('market_id') for r in recent]}"
