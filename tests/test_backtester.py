"""Tests 2 + 3: Backtester simulation and bootstrap tier logic."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.models import BacktestResult, MarketAnalysis


def _make_analysis(
    market_id: str = "mkt1",
    fair_value: float = 0.70,
    current_price: float = 0.55,
    confidence: float = 0.65,
    category: str = "politics",
) -> MarketAnalysis:
    return MarketAnalysis(
        market_id=market_id,
        platform="polymarket",
        question="Will X happen?",
        category=category,
        current_price=current_price,
        estimated_fair_value=fair_value,
        edge=fair_value - current_price,
        confidence=confidence,
        reasoning="Test reasoning",
    )


def _make_episodes(n: int, outcome: str = "Yes") -> list[dict]:
    """Create n fake resolved episodes with consistent outcomes."""
    return [
        {
            "market_id": f"seed_mkt_{i}",
            "platform": "manifold",
            "question": f"Will X happen? variant {i}",
            "category": "politics",
            "outcome": outcome,
            "close_price": 0.60,
            "source": "seed",
            "_document": f"Will X happen? variant {i}",
        }
        for i in range(n)
    ]


# ── Test 2: _simulate_trades ─────────────────────────────────────────────────

def test_simulate_wins_when_outcome_matches_direction():
    """buy_yes direction with Yes outcome should produce positive EV."""
    from agents.backtester import Backtester

    bt = Backtester(clients={}, episodic=MagicMock())

    analysis = _make_analysis(fair_value=0.70, current_price=0.55)
    # 10 episodes all resolving Yes — analyst predicted Yes (positive edge)
    episodes = _make_episodes(10, outcome="Yes")

    result = bt._simulate_trades(analysis, episodes)

    assert result.simulated_win_rate == pytest.approx(1.0)
    assert result.simulated_ev > 0
    assert result.similar_markets_found == 10


def test_simulate_losses_when_outcome_mismatches():
    """buy_yes direction with No outcome should produce negative EV."""
    from agents.backtester import Backtester

    bt = Backtester(clients={}, episodic=MagicMock())

    analysis = _make_analysis(fair_value=0.70, current_price=0.55)
    episodes = _make_episodes(10, outcome="No")

    result = bt._simulate_trades(analysis, episodes)

    assert result.simulated_win_rate == pytest.approx(0.0)
    assert result.simulated_ev < 0


def test_simulate_mixed_outcomes():
    """6 wins, 4 losses should give win_rate=0.6."""
    from agents.backtester import Backtester

    bt = Backtester(clients={}, episodic=MagicMock())
    analysis = _make_analysis(fair_value=0.70, current_price=0.55)

    yes_eps = _make_episodes(6, outcome="Yes")
    no_eps = _make_episodes(4, outcome="No")
    for i, ep in enumerate(no_eps):
        ep["market_id"] = f"seed_mkt_no_{i}"  # avoid ID collision
    episodes = yes_eps + no_eps

    result = bt._simulate_trades(analysis, episodes)

    assert result.simulated_win_rate == pytest.approx(0.6)
    assert result.similar_markets_found == 10


# ── Test 3: bootstrap tier detection ─────────────────────────────────────────

def test_bootstrap_tier0_blocks(tmp_data_dir):
    """0 resolved episodes → tier 0 → passed=False."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"
    config.MEMORY_PATH = tmp_data_dir / "memory"

    mock_episodic = MagicMock()
    mock_episodic.recall.return_value = []  # no episodes at all

    from agents.backtester import Backtester
    bt = Backtester(clients={}, episodic=mock_episodic)

    analysis = _make_analysis()
    result = bt.validate(analysis)

    assert result.passed is False
    assert "Bootstrap tier 0" in result.details


def test_bootstrap_tier1_allows_micro_position(tmp_data_dir):
    """1-3 resolved episodes → tier 1 → passed=True with micro limit."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"
    config.MEMORY_PATH = tmp_data_dir / "memory"

    # Return 2 resolved episodes (tier 1)
    mock_episodic = MagicMock()
    mock_episodic.recall.return_value = _make_episodes(2, outcome="Yes")

    from agents.backtester import Backtester
    bt = Backtester(clients={}, episodic=mock_episodic)

    analysis = _make_analysis()
    result = bt.validate(analysis)

    assert result.passed is True
    assert "Bootstrap tier 1" in result.details


def test_bootstrap_tier2_allows_reduced_position(tmp_data_dir):
    """4-7 resolved episodes → tier 2 → passed=True."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"
    config.MEMORY_PATH = tmp_data_dir / "memory"

    mock_episodic = MagicMock()
    mock_episodic.recall.return_value = _make_episodes(5, outcome="Yes")

    from agents.backtester import Backtester
    bt = Backtester(clients={}, episodic=mock_episodic)

    analysis = _make_analysis()
    result = bt.validate(analysis)

    assert result.passed is True
    assert "Bootstrap tier 2" in result.details


def test_full_backtest_passes_with_good_stats(tmp_data_dir):
    """8+ episodes with strong EV and win_rate → passed=True."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"
    config.MEMORY_PATH = tmp_data_dir / "memory"

    mock_episodic = MagicMock()
    # 10 episodes resolving Yes → 100% win rate for buy_yes direction
    mock_episodic.recall.return_value = _make_episodes(10, outcome="Yes")

    from agents.backtester import Backtester
    bt = Backtester(clients={}, episodic=mock_episodic)

    analysis = _make_analysis(fair_value=0.70, current_price=0.55)
    result = bt.validate(analysis)

    assert result.passed is True
    assert result.simulated_win_rate == pytest.approx(1.0)
