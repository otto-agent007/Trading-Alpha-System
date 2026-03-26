"""Test 4: Strategist Kelly sizing math."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from core.models import BacktestResult, MarketAnalysis


def _make_analysis(
    edge: float = 0.12,
    confidence: float = 0.60,
    fair_value: float = 0.62,
    current_price: float = 0.50,
) -> MarketAnalysis:
    return MarketAnalysis(
        market_id="mkt1",
        platform="polymarket",
        question="Test question?",
        category="politics",
        current_price=current_price,
        estimated_fair_value=fair_value,
        edge=edge,
        confidence=confidence,
        reasoning="Test",
    )


def _make_backtest(
    passed: bool = True,
    win_rate: float = 0.60,
    ev: float = 0.05,
    n: int = 10,
) -> BacktestResult:
    return BacktestResult(
        market_id="mkt1",
        similar_markets_found=n,
        simulated_win_rate=win_rate,
        simulated_ev=ev,
        simulated_max_drawdown=0.15,
        avg_entry_price=0.50,
        passed=passed,
        details=f"Test backtest. n={n}",
    )


def test_kelly_positive_edge():
    """Quarter-Kelly should return a positive fraction for valid inputs."""
    from agents.strategist import Strategist
    s = Strategist(router=MagicMock(), working=MagicMock())

    k = s._kelly(win_rate=0.60, entry_price=0.50)
    # Full Kelly = (0.6*1 - 0.4) / 1 = 0.2; quarter = 0.05
    assert k == pytest.approx(0.05, rel=0.01)


def test_kelly_degenerate_inputs():
    """Kelly returns 0 for degenerate inputs."""
    from agents.strategist import Strategist
    s = Strategist(router=MagicMock(), working=MagicMock())

    assert s._kelly(win_rate=0.0, entry_price=0.50) == 0.0
    assert s._kelly(win_rate=0.60, entry_price=0.0) == 0.0
    assert s._kelly(win_rate=0.60, entry_price=1.0) == 0.0


def test_kelly_capped_at_max_position_pct():
    """Kelly size never exceeds SP.max_position_pct."""
    from agents.strategist import Strategist
    from core.strategy_params import SP

    s = Strategist(router=MagicMock(), working=MagicMock())
    # Absurdly high win rate to force uncapped Kelly >> max_position_pct
    k = s._kelly(win_rate=0.99, entry_price=0.10)
    assert k <= SP.max_position_pct


def test_decide_produces_buy_yes_for_positive_edge():
    """Positive edge → buy_yes action."""
    from agents.strategist import Strategist
    from core.memory.working import WorkingMemory

    wm = WorkingMemory(bankroll=1000.0)
    s = Strategist(router=MagicMock(), working=wm)

    analysis = _make_analysis(edge=0.12, fair_value=0.62, current_price=0.50)
    backtest = _make_backtest()
    decision = s.decide(analysis, backtest)

    assert decision.action == "buy_yes"
    assert decision.target_price == pytest.approx(0.62)
    assert decision.size_usd > 0


def test_decide_pass_on_failing_backtest():
    """Failed backtest → pass action regardless of edge."""
    from agents.strategist import Strategist
    from core.memory.working import WorkingMemory

    wm = WorkingMemory(bankroll=1000.0)
    s = Strategist(router=MagicMock(), working=wm)

    analysis = _make_analysis(edge=0.20)
    backtest = _make_backtest(passed=False)
    decision = s.decide(analysis, backtest)

    assert decision.action == "pass"


def test_decide_pass_on_low_confidence():
    """Confidence below SP.min_confidence → pass."""
    from agents.strategist import Strategist
    from core.memory.working import WorkingMemory
    from core.strategy_params import SP

    wm = WorkingMemory(bankroll=1000.0)
    s = Strategist(router=MagicMock(), working=wm)

    analysis = _make_analysis(confidence=SP.min_confidence - 0.01)
    backtest = _make_backtest()
    decision = s.decide(analysis, backtest)

    assert decision.action == "pass"
