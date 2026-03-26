from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx
import pandas as pd

from clients.base import MarketClient
from core.models import Market, MarketPage, Orderbook, Trade

logger = logging.getLogger(__name__)

CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma.polymarket.com"


class PolymarketClient(MarketClient):
    """Polymarket CLOB + Gamma API client (read-only).

    Uses the CLOB API for orderbook, trades, and price history.
    Uses the Gamma API for richer market metadata (descriptions, categories).
    """

    def __init__(self, timeout: float = 30.0) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal HTTP helper with retry
    # ------------------------------------------------------------------

    def _get(self, url: str, params: dict | None = None, retries: int = 3, backoff: float = 2.0):
        """GET with exponential backoff. Handles transient DNS failures on Windows.

        Does NOT retry on 401 (auth required) or 404 (not found) — those aren't transient.
        """
        last_exc: Exception | None = None
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=self._timeout) as client:
                    r = client.get(url, params=params)
                    r.raise_for_status()
                    return r.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 404):
                    raise  # not transient, don't retry
                last_exc = e
                if attempt < retries - 1:
                    wait = backoff * (attempt + 1)
                    logger.debug(f"GET {url} failed (attempt {attempt + 1}), retrying in {wait}s: {e}")
                    time.sleep(wait)
            except Exception as e:
                last_exc = e
                if attempt < retries - 1:
                    wait = backoff * (attempt + 1)
                    logger.debug(f"GET {url} failed (attempt {attempt + 1}), retrying in {wait}s: {e}")
                    time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # MarketClient interface
    # ------------------------------------------------------------------

    def list_markets(
        self,
        active: bool = True,
        limit: int = 100,
        cursor: str | None = None,
    ) -> MarketPage:
        """List markets using the CLOB API (no DNS issues, always reachable)."""
        params: dict = {"limit": limit}
        if active:
            params["active"] = "true"
        if cursor:
            params["next_cursor"] = cursor

        resp = self._get(f"{CLOB_BASE}/markets", params=params)
        raw: list[dict] = resp.get("data", []) if isinstance(resp, dict) else resp
        next_cursor = resp.get("next_cursor") if isinstance(resp, dict) else None
        # CLOB returns "LTE=" as a sentinel meaning "no more pages"
        if next_cursor in ("", "LTE="):
            next_cursor = None
        markets = [self._parse_clob_market(m) for m in raw]
        return MarketPage(markets=markets, next_cursor=next_cursor)

    def get_market(self, market_id: str) -> Market:
        return self._parse_clob_market(self._get(f"{CLOB_BASE}/markets/{market_id}"))

    def get_orderbook(self, market_id: str) -> Orderbook:
        token_id = self._resolve_yes_token(market_id)
        if not token_id:
            return Orderbook(market_id=market_id)

        data = self._get(f"{CLOB_BASE}/book", params={"token_id": token_id})
        bids = [(float(b["price"]), float(b["size"])) for b in data.get("bids", [])]
        asks = [(float(a["price"]), float(a["size"])) for a in data.get("asks", [])]

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 1.0
        spread = best_ask - best_bid
        mid = (best_bid + best_ask) / 2

        return Orderbook(
            market_id=market_id,
            bids=bids,
            asks=asks,
            spread=spread,
            mid_price=mid,
        )

    def get_trades(self, market_id: str, limit: int = 100) -> list[Trade]:
        token_id = self._resolve_yes_token(market_id)
        if not token_id:
            return []

        data = self._get(f"{CLOB_BASE}/trades", params={"token_id": token_id, "limit": limit})
        trades: list[Trade] = []
        for t in data:
            try:
                trades.append(
                    Trade(
                        market_id=market_id,
                        price=float(t.get("price", 0)),
                        size_usd=float(t.get("size", 0)),
                        side=t.get("side", "buy").lower(),
                        timestamp=datetime.fromisoformat(
                            t["timestamp"].replace("Z", "+00:00")
                        )
                        if "timestamp" in t
                        else datetime.now(timezone.utc),
                    )
                )
            except Exception as e:
                logger.debug(f"Skipping malformed trade: {e}")
        return trades

    def get_price_history(
        self,
        market_id: str,
        interval: str = "1h",
    ) -> pd.DataFrame:
        """Fetch OHLC candles from Polymarket's prices-history endpoint."""
        token_id = self._resolve_yes_token(market_id)
        if not token_id:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        fidelity_map = {"1m": 1, "5m": 5, "1h": 60, "1d": 1440}
        fidelity = fidelity_map.get(interval, 60)

        data = self._get(
            f"{CLOB_BASE}/prices-history",
            params={"market": market_id, "interval": "max", "fidelity": fidelity},
        )
        if not data or "history" not in data:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        rows = []
        for point in data["history"]:
            rows.append({
                "timestamp": datetime.fromtimestamp(point["t"], tz=timezone.utc),
                "open": float(point.get("o", point.get("p", 0))),
                "high": float(point.get("h", point.get("p", 0))),
                "low": float(point.get("l", point.get("p", 0))),
                "close": float(point.get("c", point.get("p", 0))),
                "volume": float(point.get("v", 0)),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df.sort_values("timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)
        return df

    # ------------------------------------------------------------------
    # Live order placement (Phase 3 — requires POLYMARKET_PRIVATE_KEY)
    # ------------------------------------------------------------------

    def place_order(
        self,
        market_id: str,
        side: str,
        token_type: str,
        price: float,
        size_usd: float,
    ) -> dict | None:
        """Place a GTC limit order on the Polymarket CLOB.

        Requires the ``py-clob-client`` package and POLYMARKET_PRIVATE_KEY env var.
        Returns the CLOB API response dict on success, None on failure.

        Args:
            market_id:  Polymarket condition ID.
            side:       "BUY" or "SELL".
            token_type: "yes" or "no" — which outcome token to trade.
            price:      Limit price (0.0–1.0).
            size_usd:   Notional USD amount to spend (converted to shares internally).
        """
        from config import POLYMARKET_PRIVATE_KEY

        if not POLYMARKET_PRIVATE_KEY:
            logger.error("place_order: POLYMARKET_PRIVATE_KEY not set — cannot place live order")
            return None

        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds, OrderArgs, PartialCreateOrderOptions
            from py_clob_client.constants import POLYGON
        except ImportError:
            logger.error(
                "place_order: py-clob-client not installed. "
                "Run: pip install py-clob-client"
            )
            return None

        try:
            # Resolve the correct token ID for the requested outcome
            token_id = self._resolve_token(market_id, token_type)
            if not token_id:
                logger.error(f"place_order: could not resolve {token_type} token for {market_id}")
                return None

            # Convert USD amount to shares: shares = size_usd / price
            shares = round(size_usd / price, 2) if price > 0 else 0
            if shares <= 0:
                logger.error(f"place_order: computed zero shares for size={size_usd}, price={price}")
                return None

            clob = ClobClient(
                host=CLOB_BASE,
                key=POLYMARKET_PRIVATE_KEY,
                chain_id=POLYGON,
            )
            # Derive session credentials from the private key
            clob.set_api_creds(clob.derive_api_key())

            order_args = OrderArgs(
                token_id=token_id,
                price=round(price, 4),
                size=shares,
                side=side,
            )
            signed_order = clob.create_order(order_args)
            resp = clob.post_order(signed_order, PartialCreateOrderOptions(tick_size=0.01))
            logger.info(f"place_order: {side} {shares:.2f} shares of {token_type} @ {price:.4f} → {resp}")
            return resp

        except Exception as e:
            logger.error(f"place_order failed for {market_id}: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_token(self, market_id: str, token_type: str) -> str | None:
        """Look up the YES or NO outcome token ID for a market."""
        try:
            data = self._get(f"{CLOB_BASE}/markets/{market_id}")
            tokens = data.get("tokens", [])
            for tok in tokens:
                if tok.get("outcome", "").lower() == token_type.lower():
                    return tok.get("token_id")
            # Fallback: YES=index 0, NO=index 1
            if token_type.lower() == "yes" and tokens:
                return tokens[0].get("token_id")
            if token_type.lower() == "no" and len(tokens) > 1:
                return tokens[1].get("token_id")
            return None
        except Exception as e:
            logger.warning(f"Could not resolve {token_type} token for {market_id}: {e}")
            return None

    def _resolve_yes_token(self, market_id: str) -> str | None:
        """Look up the YES outcome token ID for a market (delegates to _resolve_token)."""
        return self._resolve_token(market_id, "yes")

    @staticmethod
    def _parse_gamma_market(m: dict) -> Market:
        """Parse a raw Gamma API market response into our Market model."""
        outcome_prices = m.get("outcomePrices") or []
        prices: dict[str, float] = {}
        if len(outcome_prices) >= 2:
            try:
                prices = {"Yes": float(outcome_prices[0]), "No": float(outcome_prices[1])}
            except (ValueError, TypeError):
                pass

        close_date = None
        for date_field in ("endDate", "endDateIso"):
            raw_date = m.get(date_field)
            if raw_date:
                try:
                    close_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    break
                except Exception:
                    pass

        resolved_price = m.get("resolvedPrice")
        if m.get("closed") and resolved_price is not None:
            status = "resolved"
            resolved_outcome = "Yes" if float(resolved_price) == 1.0 else "No"
        elif m.get("closed"):
            status = "closed"
            resolved_outcome = None
        else:
            status = "open"
            resolved_outcome = None

        return Market(
            id=m.get("id", m.get("conditionId", "")),
            platform="polymarket",
            question=m.get("question", ""),
            description=m.get("description", ""),
            category=(m.get("category") or "other").lower().strip(),
            outcomes=["Yes", "No"],
            current_prices=prices,
            volume_usd=float(m.get("volume") or 0),
            liquidity_usd=float(m.get("liquidity") or 0),
            close_date=close_date,
            status=status,
            resolved_outcome=resolved_outcome,
        )

    @staticmethod
    def _parse_clob_market(m: dict) -> Market:
        """Parse a raw CLOB API market response into our Market model.

        CLOB field names differ from Gamma: condition_id, volume_num, end_date_iso, etc.
        """
        # Prices: tokens list has [{outcome: "Yes", price: "0.65"}, {outcome: "No", ...}]
        prices: dict[str, float] = {}
        for tok in m.get("tokens", []):
            outcome = tok.get("outcome", "")
            price_raw = tok.get("price")
            if outcome and price_raw is not None:
                try:
                    prices[outcome] = float(price_raw)
                except (ValueError, TypeError):
                    pass

        close_date = None
        for date_field in ("end_date_iso", "endDateIso", "end_date"):
            raw_date = m.get(date_field)
            if raw_date:
                try:
                    close_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                    break
                except Exception:
                    pass

        closed = m.get("closed", False)
        active = m.get("active", True)
        # CLOB doesn't expose resolved outcome directly; use closed+inactive as proxy
        if closed and not active:
            status = "resolved"
            resolved_outcome = None  # not available in CLOB list endpoint
        elif closed:
            status = "closed"
            resolved_outcome = None
        else:
            status = "open"
            resolved_outcome = None

        market_id = m.get("condition_id", m.get("conditionId", m.get("id", "")))

        return Market(
            id=market_id,
            platform="polymarket",
            question=m.get("question", ""),
            description=m.get("description", ""),
            category=(m.get("category") or "other").lower().strip(),
            outcomes=["Yes", "No"],
            current_prices=prices,
            volume_usd=float(m.get("volume_num", m.get("volume") or 0) or 0),
            liquidity_usd=float(m.get("liquidity_num", m.get("liquidity") or 0) or 0),
            close_date=close_date,
            status=status,
            resolved_outcome=resolved_outcome,
        )
