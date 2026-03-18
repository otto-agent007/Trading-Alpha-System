from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx
import pandas as pd

from clients.base import MarketClient
from config import KALSHI_API_KEY, KALSHI_API_SECRET
from core.models import Market, MarketPage, Orderbook, Trade

logger = logging.getLogger(__name__)

BASE_URL = "https://trading-api.kalshi.com/trade-api/v2"


class KalshiClient(MarketClient):
    """Kalshi REST API client (read-only).

    Kalshi requires authentication for all endpoints.
    If KALSHI_API_KEY is not set, all methods return empty results
    and log a warning.
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout
        self._api_key = KALSHI_API_KEY
        self._api_secret = KALSHI_API_SECRET
        self._has_creds = bool(self._api_key)
        if not self._has_creds:
            logger.warning(
                "KALSHI_API_KEY not set — Kalshi client will return empty results. "
                "Set KALSHI_API_KEY and KALSHI_API_SECRET to enable."
            )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # MarketClient interface
    # ------------------------------------------------------------------

    def list_markets(
        self,
        active: bool = True,
        limit: int = 100,
        cursor: str | None = None,
    ) -> MarketPage:
        if not self._has_creds:
            return MarketPage(markets=[])

        params: dict = {"limit": limit}
        if active:
            params["status"] = "open"
        if cursor:
            params["cursor"] = cursor

        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(
                f"{BASE_URL}/markets",
                headers=self._headers(),
                params=params,
            )
            r.raise_for_status()
            body = r.json()

        raw_markets = body.get("markets", [])
        markets = [self._parse_market(m) for m in raw_markets]
        next_cursor = body.get("cursor")
        return MarketPage(markets=markets, next_cursor=next_cursor)

    def get_market(self, market_id: str) -> Market:
        if not self._has_creds:
            return Market(id=market_id, platform="kalshi", question="")

        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(
                f"{BASE_URL}/markets/{market_id}",
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json().get("market", r.json())
        return self._parse_market(data)

    def get_orderbook(self, market_id: str) -> Orderbook:
        if not self._has_creds:
            return Orderbook(market_id=market_id)

        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(
                f"{BASE_URL}/markets/{market_id}/orderbook",
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json().get("orderbook", r.json())

        bids = [
            (float(b[0]) / 100, float(b[1]))
            for b in data.get("yes", data.get("bids", []))
        ]
        asks = [
            (float(a[0]) / 100, float(a[1]))
            for a in data.get("no", data.get("asks", []))
        ]

        best_bid = bids[0][0] if bids else 0.0
        best_ask = 1.0 - (asks[0][0] if asks else 0.0)
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2

        return Orderbook(
            market_id=market_id,
            bids=bids,
            asks=asks,
            spread=max(spread, 0.0),
            mid_price=mid,
        )

    def get_trades(self, market_id: str, limit: int = 100) -> list[Trade]:
        if not self._has_creds:
            return []

        with httpx.Client(timeout=self._timeout) as client:
            r = client.get(
                f"{BASE_URL}/markets/{market_id}/trades",
                headers=self._headers(),
                params={"limit": limit},
            )
            r.raise_for_status()
            data = r.json().get("trades", [])

        trades: list[Trade] = []
        for t in data:
            try:
                ts = t.get("created_time") or t.get("ts")
                trades.append(
                    Trade(
                        market_id=market_id,
                        price=float(t.get("yes_price", t.get("price", 0))) / 100,
                        size_usd=float(t.get("count", t.get("size", 0))),
                        side=t.get("taker_side", "buy").lower(),
                        timestamp=datetime.fromisoformat(
                            ts.replace("Z", "+00:00")
                        )
                        if ts
                        else datetime.now(timezone.utc),
                    )
                )
            except Exception as e:
                logger.debug(f"Skipping malformed Kalshi trade: {e}")
        return trades

    def get_price_history(
        self,
        market_id: str,
        interval: str = "1h",
    ) -> pd.DataFrame:
        """Kalshi doesn't expose OHLC candles publicly.
        Build synthetic candles from trade history instead."""
        trades = self.get_trades(market_id, limit=500)
        if not trades:
            return pd.DataFrame(
                columns=["timestamp", "open", "high", "low", "close", "volume"]
            )

        df = pd.DataFrame([t.model_dump() for t in trades])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df.set_index("timestamp", inplace=True)

        freq_map = {"1m": "1min", "5m": "5min", "1h": "1h", "1d": "1D"}
        freq = freq_map.get(interval, "1h")

        ohlc = df["price"].resample(freq).agg(["first", "max", "min", "last"])
        ohlc.columns = ["open", "high", "low", "close"]
        ohlc["volume"] = df["size_usd"].resample(freq).sum()
        ohlc.dropna(subset=["open"], inplace=True)
        ohlc.reset_index(inplace=True)
        ohlc.rename(columns={"index": "timestamp"}, inplace=True)
        return ohlc

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_market(m: dict) -> Market:
        """Parse a raw Kalshi market response into our Market model."""
        # Kalshi prices are in cents (0-100)
        yes_price = float(m.get("yes_bid", m.get("last_price", 50))) / 100
        no_price = 1.0 - yes_price

        close_time = m.get("close_time") or m.get("expiration_time")
        close_date = None
        if close_time:
            try:
                close_date = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            except Exception:
                pass

        result = m.get("result")
        if result is not None:
            status = "resolved"
            resolved_outcome = "Yes" if result == "yes" else "No"
        elif m.get("status") == "closed":
            status = "closed"
            resolved_outcome = None
        else:
            status = "open"
            resolved_outcome = None

        return Market(
            id=m.get("ticker", m.get("id", "")),
            platform="kalshi",
            question=m.get("title", m.get("subtitle", "")),
            description=m.get("subtitle", m.get("rules_primary", "")),
            category=(m.get("category") or "other").lower().strip(),
            outcomes=["Yes", "No"],
            current_prices={"Yes": yes_price, "No": no_price},
            volume_usd=float(m.get("volume", 0)),
            liquidity_usd=float(m.get("open_interest", 0)),
            close_date=close_date,
            status=status,
            resolved_outcome=resolved_outcome,
        )
