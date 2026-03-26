"""Fetch resolved markets from the Manifold API for backtest seeding.

Downloads binary YES/NO-resolved markets from Manifold (440K+ available) and
writes them to DATA_PATH/linux_shared/resolved_markets.json in the format that
scripts/seed_memory.py already knows how to ingest.

After running this, run:
    python scripts/seed_memory.py

That seeds episodic memory so the backtester's bootstrap tier-0 block no longer
fires and Kelly-sized trades can proceed from day one.

Usage:
    python scripts/fetch_manifold.py                   # fetch 5000 markets (default)
    python scripts/fetch_manifold.py --limit 2000      # fewer for a quick test
    python scripts/fetch_manifold.py --limit 10000     # larger corpus
    python scripts/fetch_manifold.py --dry-run         # count without writing

Rate limit: Manifold allows ~60 req/min (public, no auth needed).
Each page fetches up to 1000 markets.  5000 markets = ~5 pages = well under limit.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Allow imports from project root
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

MANIFOLD_API = "https://manifold.markets/api/v0/markets"
OUTPUT_FILE = DATA_PATH / "linux_shared" / "resolved_markets.json"

# Manifold page size (API max is 1000)
PAGE_SIZE = 1000


def fetch_manifold_resolved(limit: int = 5000) -> list[dict]:
    """Fetch up to `limit` resolved binary markets from Manifold.

    Uses cursor-based pagination via the `before` parameter (last market ID of
    previous page).  Stops when limit is reached or no more pages exist.
    """
    markets: list[dict] = []
    cursor: str | None = None
    page = 0

    logger.info(f"Fetching up to {limit} resolved markets from Manifold API...")

    with httpx.Client(timeout=30.0) as client:
        while len(markets) < limit:
            page += 1
            params: dict = {
                "limit": min(PAGE_SIZE, limit - len(markets)),
                "sort": "newest",
                "filter": "resolved",
            }
            if cursor:
                params["before"] = cursor

            try:
                resp = client.get(MANIFOLD_API, params=params)
                resp.raise_for_status()
                page_data: list[dict] = resp.json()
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error on page {page}: {e}")
                break
            except Exception as e:
                logger.error(f"Request failed on page {page}: {e}")
                break

            if not page_data:
                logger.info(f"No more data after page {page - 1}")
                break

            # Filter: only binary markets with a clear YES/NO resolution
            binary = [
                m for m in page_data
                if m.get("outcomeType") in ("BINARY", "PSEUDO_NUMERIC")
                and str(m.get("resolution", "")).upper() in ("YES", "NO")
            ]

            markets.extend(binary)
            cursor = page_data[-1]["id"] if page_data else None

            logger.info(
                f"Page {page}: fetched {len(page_data)} total, "
                f"{len(binary)} binary resolved, "
                f"cumulative {len(markets)}/{limit}"
            )

            # Respect Manifold rate limit
            if len(markets) < limit and cursor:
                time.sleep(1.1)  # ~55 req/min — comfortably under the 60/min limit

    logger.info(f"Fetched {len(markets)} binary resolved markets total")
    return markets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch resolved Manifold markets for backtest seeding"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max markets to fetch (default: 5000)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=str(OUTPUT_FILE),
        help=f"Output JSON file path (default: {OUTPUT_FILE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and count without writing to disk",
    )
    args = parser.parse_args()

    markets = fetch_manifold_resolved(limit=args.limit)

    if not markets:
        logger.error("No markets fetched — check network connectivity")
        sys.exit(1)

    # Category breakdown
    from collections import Counter
    cats = Counter(
        (m.get("groupSlugs", ["other"])[0] if m.get("groupSlugs") else "other").lower()
        for m in markets
    )
    resolutions = Counter(str(m.get("resolution", "?")).upper() for m in markets)

    print(f"\n{'='*60}")
    print(f"Fetched {len(markets)} binary resolved markets")
    print(f"{'='*60}")
    print(f"\nResolutions: {dict(resolutions)}")
    print(f"\nTop categories:")
    for cat, count in cats.most_common(15):
        print(f"  {cat}: {count}")

    if args.dry_run:
        print("\n[DRY RUN] Not writing to disk.")
        return

    # Ensure output directory exists
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # seed_memory.py parse_manifold() expects the list directly, tagged with platform
    # We normalise here so the parser works even without groupSlugs.
    for m in markets:
        m["platform"] = "manifold"  # ensure parser picks the right branch

    out_path.write_text(json.dumps(markets, indent=2), encoding="utf-8")
    logger.info(f"Wrote {len(markets)} markets to {out_path}")

    print(f"\nWrote {len(markets)} markets to:\n  {out_path}")
    print("\nNext step — seed episodic memory:")
    print(f"  python scripts/seed_memory.py {out_path}")
    print("\nThen verify the backtester is no longer in bootstrap tier-0:")
    print(
        "  python -c \"from core.memory.episodic import EpisodicMemory; "
        "e = EpisodicMemory(); print('Episodes:', e.count())\""
    )


if __name__ == "__main__":
    main()
