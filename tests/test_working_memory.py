"""Test 1: WorkingMemory PnL arithmetic.

Verifies record_position + resolve_position bankroll calculations.
This is the most financially critical test — wrong PnL means wrong bankroll.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.models import Market, TradeDecision


def _make_market(market_id: str = "mkt1", platform: str = "polymarket") -> Market:
    return Market(
        id=market_id,
        platform=platform,  # type: ignore[arg-type]
        question="Will X happen?",
        category="other",
        current_prices={"Yes": 0.65, "No": 0.35},
        volume_usd=10_000,
    )


def _make_decision(
    market_id: str = "mkt1",
    action: str = "buy_yes",
    price: float = 0.65,
    size: float = 50.0,
) -> TradeDecision:
    return TradeDecision(
        market_id=market_id,
        platform="polymarket",
        action=action,  # type: ignore[arg-type]
        target_price=price,
        size_usd=size,
        kelly_fraction=0.05,
        paper_only=True,
    )


def test_bankroll_decrements_on_open(tmp_data_dir):
    """Opening a position deducts size_usd from bankroll."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"

    from core.memory.working import WorkingMemory
    wm = WorkingMemory(bankroll=1000.0)

    decision = _make_decision(size=50.0)
    market = _make_market()
    wm.record_position(decision, market)

    assert wm.bankroll == pytest.approx(950.0)
    assert len(wm.open_positions()) == 1


def test_winning_yes_pnl(tmp_data_dir):
    """WIN on buy_yes: pnl = size * (1 - entry) / entry, bankroll restored + profit."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"

    from core.memory.working import WorkingMemory
    wm = WorkingMemory(bankroll=1000.0)

    price = 0.65
    size = 65.0
    wm.record_position(_make_decision(price=price, size=size, action="buy_yes"), _make_market())

    pnl = wm.resolve_position("mkt1", "Yes")

    expected_pnl = size * (1.0 - price) / price  # ~35.0
    assert pnl == pytest.approx(expected_pnl, rel=1e-6)
    # bankroll: 1000 - 65 + 65 + pnl = 1000 + pnl
    assert wm.bankroll == pytest.approx(1000.0 + expected_pnl, rel=1e-6)
    assert wm.open_positions() == []


def test_losing_yes_pnl(tmp_data_dir):
    """LOSS on buy_yes (resolved No): pnl = -size_usd, bankroll drops by size."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"

    from core.memory.working import WorkingMemory
    wm = WorkingMemory(bankroll=1000.0)

    size = 50.0
    wm.record_position(_make_decision(size=size, action="buy_yes"), _make_market())
    pnl = wm.resolve_position("mkt1", "No")

    assert pnl == pytest.approx(-size)
    assert wm.bankroll == pytest.approx(1000.0 - size)


def test_winning_no_pnl(tmp_data_dir):
    """WIN on buy_no (resolved No): pnl = size * (1 - entry) / entry."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"

    from core.memory.working import WorkingMemory
    wm = WorkingMemory(bankroll=1000.0)

    price = 0.35
    size = 35.0
    # For buy_no the target_price field represents the NO price
    decision = _make_decision(action="buy_no", price=price, size=size)
    wm.record_position(decision, _make_market())

    pnl = wm.resolve_position("mkt1", "No")

    expected_pnl = size * (1.0 - price) / price
    assert pnl == pytest.approx(expected_pnl, rel=1e-6)
    assert wm.bankroll == pytest.approx(1000.0 + expected_pnl, rel=1e-6)


def test_total_exposure(tmp_data_dir):
    """total_exposure() sums all open position sizes."""
    import config
    config.DATA_PATH = tmp_data_dir / "data"

    from core.memory.working import WorkingMemory
    wm = WorkingMemory(bankroll=1000.0)

    wm.record_position(_make_decision(market_id="m1", size=30.0), _make_market("m1"))
    wm.record_position(_make_decision(market_id="m2", size=20.0), _make_market("m2"))

    assert wm.total_exposure() == pytest.approx(50.0)
