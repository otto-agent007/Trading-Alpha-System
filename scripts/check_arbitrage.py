"""Fetch cross-platform arbitrage opportunities from Brier.fyi and write to
DATA_PATH/linux_shared/arbitrage_alerts.json.

The scanner reads arbitrage_alerts.json on each cycle via LINUX.get_arbitrage_alerts()
and adds flagged markets to the watchlist at score 0.99 (highest priority).

Brier.fyi is a PostgREST API that links the same question across prediction platforms
and exposes price discrepancies.  Run this script on a schedule (e.g. every 30 min)
from the Linux box or a cron job.

Usage:
    python scripts/check_arbitrage.py                  # write alerts
    python scripts/check_arbitrage.py --dry-run        # print without writing
    python scripts/check_arbitrage.py --min-edge 0.05  # only alerts with >=5% spread

NOTE: Brier.fyi API endpoint and schema may change.  If the request fails, the script
writes an empty alerts file so the scanner degrades gracefully (no stale alerts).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx

from config import DATA_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

OUTPUT_FILE = DATA_PATH / "linux_shared" / "arbitrage_alerts.json"

# Brier.fyi PostgREST API — returns markets linked across platforms with price deltas.
# Adjust the base URL / table / column names if the schema changes.
BRIER_API = "https://brier.fyi/api"


def fetch_brier_arb(min_edge: float = 0.04) -> list[dict]:
    """Fetch cross-platform price discrepancies from Brier.fyi.

    Returns a list of alert dicts with keys:
        market_id, platform, question, category, edge_pct, poly_price, kalshi_price

    Falls back to empty list on any error so the caller can write a clean file.
    """
    try:
        # PostgREST filter: spread >= min_edge, ordered by spread desc
        resp = httpx.get(
            f"{BRIER_API}/linked_markets",
            params={
                "spread": f"gte.{min_edge}",
                "order": "spread.desc",
                "limit": "50",
            },
            timeout=15.0,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        raw: list[dict] = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning(f"Brier.fyi HTTP error: {e}")
        return []
    except Exception as e:
        logger.warning(f"Brier.fyi request failed: {e}")
        return []

    alerts: list[dict] = []
    for row in raw:
        # Normalise field names — Brier.fyi schema as of early 2026
        # Primary market is whichever platform has the lower (buying) price
        poly_price = row.get("polymarket_price") or row.get("poly_price")
        kalshi_price = row.get("kalshi_price")
        spread = row.get("spread") or row.get("edge_pct")

        if spread is None:
            continue

        try:
            spread = float(spread)
        except (ValueError, TypeError):
            continue

        if spread < min_edge:
            continue

        # Determine which platform to trade on (the cheaper one)
        platform = "polymarket"
        price = poly_price
        if kalshi_price is not None and poly_price is not None:
            try:
                if float(kalshi_price) < float(poly_price):
                    platform = "kalshi"
                    price = kalshi_price
            except (ValueError, TypeError):
                pass

        alerts.append({
            "market_id": row.get("polymarket_id") or row.get("market_id") or "",
            "platform": platform,
            "question": row.get("question") or row.get("title") or "",
            "category": (row.get("category") or "other").lower(),
            "edge_pct": round(spread, 4),
            "poly_price": poly_price,
            "kalshi_price": kalshi_price,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        })

    return alerts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch Brier.fyi cross-platform arbitrage alerts"
    )
    parser.add_argument(
        "--min-edge",
        type=float,
        default=0.04,
        help="Minimum price spread to include (default: 0.04 = 4%%)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print alerts without writing to disk",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUTPUT_FILE),
        help=f"Output file (default: {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    alerts = fetch_brier_arb(min_edge=args.min_edge)

    logger.info(f"Found {len(alerts)} arbitrage alerts (min_edge={args.min_edge:.0%})")
    for a in alerts[:5]:
        logger.info(
            f"  {a['question'][:60]} | edge={a['edge_pct']:.1%} | "
            f"poly={a['poly_price']} kalshi={a['kalshi_price']}"
        )

    if args.dry_run:
        print(json.dumps({"alerts": alerts}, indent=2))
        return

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"alerts": alerts, "updated_at": datetime.now(timezone.utc).isoformat()}, indent=2),
        encoding="utf-8",
    )
    logger.info(f"Wrote {len(alerts)} alerts to {out_path}")


if __name__ == "__main__":
    main()
