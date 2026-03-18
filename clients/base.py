from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from core.models import Market, MarketPage, Orderbook, Trade


class MarketClient(ABC):
    """Abstract interface for prediction market platforms.

    Both PolymarketClient and KalshiClient implement this so that agents
    can work with either platform interchangeably.
    """

    @abstractmethod
    def list_markets(
        self,
        active: bool = True,
        limit: int = 100,
        cursor: str | None = None,
    ) -> MarketPage:
        """Return a page of markets, optionally filtered by active status."""
        ...

    @abstractmethod
    def get_market(self, market_id: str) -> Market:
        """Fetch a single market by ID."""
        ...

    @abstractmethod
    def get_orderbook(self, market_id: str) -> Orderbook:
        """Fetch the live orderbook for a market's YES token."""
        ...

    @abstractmethod
    def get_trades(self, market_id: str, limit: int = 100) -> list[Trade]:
        """Fetch recent trades for a market's YES token."""
        ...

    @abstractmethod
    def get_price_history(
        self,
        market_id: str,
        interval: str = "1h",
    ) -> pd.DataFrame:
        """Fetch OHLC candle data.  Returns DataFrame with columns:
        timestamp, open, high, low, close, volume."""
        ...
