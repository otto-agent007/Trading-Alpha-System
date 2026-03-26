"""Test 6: Scanner._is_alive() rejection logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.models import Market


def _market(
    market_id: str = "m1",
    status: str = "open",
    close_date: datetime | None = None,
    question: str = "Will X happen?",
    yes_price: float = 0.50,
) -> Market:
    return Market(
        id=market_id,
        platform="polymarket",  # type: ignore[arg-type]
        question=question,
        current_prices={"Yes": yes_price, "No": 1.0 - yes_price},
        volume_usd=5_000,
        status=status,  # type: ignore[arg-type]
        close_date=close_date,
    )


def _scanner():
    from agents.scanner import Scanner
    return Scanner(
        clients=[],
        router=MagicMock(),
        semantic=MagicMock(),
        working=MagicMock(),
    )


def test_open_market_is_alive():
    sc = _scanner()
    m = _market(close_date=datetime.now(timezone.utc) + timedelta(days=30))
    assert sc._is_alive(m) is True


def test_closed_market_rejected():
    sc = _scanner()
    m = _market(status="closed")
    assert sc._is_alive(m) is False


def test_resolved_market_rejected():
    sc = _scanner()
    m = _market(status="resolved")
    assert sc._is_alive(m) is False


def test_expired_close_date_rejected():
    sc = _scanner()
    m = _market(close_date=datetime.now(timezone.utc) - timedelta(days=1))
    assert sc._is_alive(m) is False


def test_past_year_reference_rejected():
    sc = _scanner()
    # Current year in tests is > 2024
    m = _market(question="2023 NBA Championship winner?")
    # Patch current year to 2026 for determinism
    with patch("agents.scanner.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 6, 1, tzinfo=timezone.utc)
        mock_dt.now.return_value.year = 2026
        # Re-use real datetime for other calls
        mock_dt.fromisoformat = datetime.fromisoformat
        assert sc._is_alive(m) is False


def test_extreme_price_rejected_low():
    sc = _scanner()
    m = _market(yes_price=0.02)
    assert sc._is_alive(m) is False


def test_extreme_price_rejected_high():
    sc = _scanner()
    m = _market(yes_price=0.98)
    assert sc._is_alive(m) is False


def test_missing_market_id_rejected():
    sc = _scanner()
    m = _market(market_id="")
    assert sc._is_alive(m) is False
