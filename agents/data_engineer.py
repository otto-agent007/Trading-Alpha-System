import httpx
import pandas as pd
import logging
from datetime import datetime, timezone
from config import DATA_PATH

logger = logging.getLogger(__name__)

GAMMA_BASE = "https://gamma.polymarket.com"


class DataEngineer:
    @staticmethod
    def fetch() -> tuple[pd.DataFrame, pd.DataFrame]:
        """Fetch historical resolved markets and currently open markets from Polymarket."""
        historical_df = DataEngineer._fetch_historical()
        open_df = DataEngineer._fetch_open()
        return historical_df, open_df

    @staticmethod
    def _fetch_historical() -> pd.DataFrame:
        """Fetch closed/resolved markets and cache to parquet."""
        cache_path = DATA_PATH / "polymarket_historical.parquet"
        markets = []
        offset = 0
        limit = 100

        with httpx.Client(timeout=30) as client:
            while True:
                try:
                    r = client.get(
                        f"{GAMMA_BASE}/markets",
                        params={"closed": "true", "limit": limit, "offset": offset},
                    )
                    r.raise_for_status()
                    batch = r.json()
                except Exception as e:
                    logger.error(f"Polymarket historical fetch error at offset {offset}: {e}")
                    break

                if not batch:
                    break

                for m in batch:
                    resolved_price = m.get("resolvedPrice")
                    if resolved_price is None:
                        continue

                    end_date = m.get("endDate") or m.get("endDateIso")
                    start_date = m.get("startDate") or m.get("startDateIso")

                    days_active = None
                    if end_date and start_date:
                        try:
                            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                            days_active = max((end_dt - start_dt).days, 0)
                        except Exception:
                            pass

                    markets.append({
                        "id": m.get("id", ""),
                        "title": m.get("question", ""),
                        "category": (m.get("category") or "other").lower().strip(),
                        "volume_usd": float(m.get("volume") or 0),
                        "resolved_yes": float(resolved_price) == 1.0,
                        "end_date": end_date,
                        "days_active": days_active,
                    })

                if len(batch) < limit:
                    break
                offset += limit

        cols = ["id", "title", "category", "volume_usd", "resolved_yes", "end_date", "days_active"]
        df = pd.DataFrame(markets) if markets else pd.DataFrame(columns=cols)

        DATA_PATH.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path, index=False)
        logger.info(f"Fetched {len(df)} historical markets from Polymarket")
        return df

    @staticmethod
    def _fetch_open() -> pd.DataFrame:
        """Fetch currently active markets and cache to parquet."""
        cache_path = DATA_PATH / "polymarket_open.parquet"
        markets = []
        now = datetime.now(timezone.utc)

        with httpx.Client(timeout=30) as client:
            try:
                r = client.get(
                    f"{GAMMA_BASE}/markets",
                    params={"active": "true", "limit": 100},
                )
                r.raise_for_status()
                data = r.json()
            except Exception as e:
                logger.error(f"Polymarket open markets fetch error: {e}")
                data = []

        for m in data:
            outcome_prices = m.get("outcomePrices") or []
            yes_prob = None
            if len(outcome_prices) >= 1:
                try:
                    yes_prob = float(outcome_prices[0])
                except Exception:
                    pass

            end_date = m.get("endDate") or m.get("endDateIso")
            days_to_close = None
            if end_date:
                try:
                    end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                    days_to_close = max((end_dt - now).days, 0)
                except Exception:
                    pass

            markets.append({
                "id": m.get("id", ""),
                "title": m.get("question", ""),
                "category": (m.get("category") or "other").lower().strip(),
                "volume_usd": float(m.get("volume") or 0),
                "yes_prob": yes_prob,
                "days_to_close": days_to_close,
                "end_date": end_date,
            })

        cols = ["id", "title", "category", "volume_usd", "yes_prob", "days_to_close", "end_date"]
        df = pd.DataFrame(markets) if markets else pd.DataFrame(columns=cols)

        DATA_PATH.mkdir(parents=True, exist_ok=True)
        df.to_parquet(cache_path, index=False)
        logger.info(f"Fetched {len(df)} open markets from Polymarket")
        return df
