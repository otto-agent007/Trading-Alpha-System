from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Market data models — shared across both platforms
# ---------------------------------------------------------------------------

class Market(BaseModel):
    id: str
    platform: Literal["polymarket", "kalshi"]
    question: str
    description: str = ""
    category: str = "other"
    outcomes: list[str] = Field(default_factory=lambda: ["Yes", "No"])
    current_prices: dict[str, float] = Field(default_factory=dict)
    volume_usd: float = 0.0
    liquidity_usd: float = 0.0
    close_date: datetime | None = None
    status: Literal["open", "closed", "resolved"] = "open"
    resolved_outcome: str | None = None


class MarketPage(BaseModel):
    markets: list[Market]
    next_cursor: str | None = None


class Orderbook(BaseModel):
    market_id: str
    bids: list[tuple[float, float]] = Field(default_factory=list)  # (price, size)
    asks: list[tuple[float, float]] = Field(default_factory=list)
    spread: float = 0.0
    mid_price: float = 0.0


class Trade(BaseModel):
    market_id: str
    price: float
    size_usd: float
    side: Literal["buy", "sell"]
    timestamp: datetime


# ---------------------------------------------------------------------------
# Agent output models
# ---------------------------------------------------------------------------

class MarketAnalysis(BaseModel):
    market_id: str
    platform: str
    question: str
    category: str = "other"
    current_price: float
    estimated_fair_value: float
    edge: float  # fair_value - current_price
    confidence: float  # 0-1
    reasoning: str
    orderbook_summary: str = ""
    similar_past_markets: list[str] = Field(default_factory=list)
    applicable_patterns: list[str] = Field(default_factory=list)


class BacktestResult(BaseModel):
    market_id: str
    similar_markets_found: int
    simulated_win_rate: float
    simulated_ev: float
    simulated_max_drawdown: float
    avg_entry_price: float
    passed: bool
    details: str = ""


class TradeDecision(BaseModel):
    id: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    market_id: str
    platform: str = ""
    action: Literal["buy_yes", "buy_no", "pass"]
    target_price: float = 0.0
    size_usd: float = 0.0
    kelly_fraction: float = 0.0
    reasoning: str = ""
    backtest_ev: float = 0.0
    backtest_sample: int = 0
    paper_only: bool = True


# ---------------------------------------------------------------------------
# Working memory models
# ---------------------------------------------------------------------------

class Position(BaseModel):
    market_id: str
    platform: str
    question: str
    category: str = "other"
    direction: Literal["yes", "no"]
    entry_price: float
    size_usd: float
    entry_time: datetime
    current_price: float = 0.0
    pnl: float = 0.0
    status: Literal["open", "closed"] = "open"
    resolved_outcome: str | None = None


class WatchlistItem(BaseModel):
    market_id: str
    platform: str
    question: str
    category: str
    added_at: datetime
    reason: str
    pattern_match_score: float = 0.0
