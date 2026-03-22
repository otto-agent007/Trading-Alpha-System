"""
Seed episodic memory from resolved markets.

Solves the bootstrap problem: the backtester needs resolved market history
to validate trades, but a fresh system has none. This script reads the Linux
box's resolved_markets.json (synced to DATA_PATH/linux_shared/ or provided
directly) and populates episodic memory with real outcomes.

After seeding, the backtester can find genuinely similar resolved markets
from day one — no blind bootstrap trades needed.

Usage:
    # From synced linux_shared directory (default)
    python scripts/seed_memory.py

    # From a specific file
    python scripts/seed_memory.py /path/to/resolved_markets.json

    # Dry run — show what would be seeded without writing
    python scripts/seed_memory.py --dry-run

    # Limit to N markets (useful for testing)
    python scripts/seed_memory.py --limit 500

    # Filter by platform
    python scripts/seed_memory.py --platform polymarket
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config import DATA_PATH
from core.memory.episodic import EpisodicMemory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Default location: synced from Linux box
DEFAULT_FILE = DATA_PATH / "linux_shared" / "resolved_markets.json"


# ---------------------------------------------------------------------------
# Parsers for different market data formats
# ---------------------------------------------------------------------------

def parse_polymarket(m: dict) -> dict | None:
    """Parse a Polymarket resolved market into a seed episode."""
    question = m.get("question", "")
    if not question:
        return None

    # Determine outcome from various field names
    outcome = None
    resolved_price = m.get("resolvedPrice") or m.get("resolved_price")
    if resolved_price is not None:
        try:
            outcome = "Yes" if float(resolved_price) == 1.0 else "No"
        except (ValueError, TypeError):
            pass

    # Some exports use "result" or "outcome" directly
    if not outcome:
        raw_outcome = m.get("result") or m.get("outcome") or m.get("resolution")
        if raw_outcome:
            raw_lower = str(raw_outcome).lower()
            if raw_lower in ("yes", "true", "1", "1.0"):
                outcome = "Yes"
            elif raw_lower in ("no", "false", "0", "0.0"):
                outcome = "No"

    if not outcome:
        return None  # skip ambiguous resolutions

    market_id = m.get("condition_id") or m.get("conditionId") or m.get("id", "")
    category = (m.get("category") or "other").lower().strip()
    volume = float(m.get("volume") or m.get("volume_num") or 0)

    # Extract closing price for richer episode data
    outcome_prices = m.get("outcomePrices") or m.get("outcome_prices") or []
    close_price = None
    if len(outcome_prices) >= 1:
        try:
            close_price = float(outcome_prices[0])
        except (ValueError, TypeError):
            pass

    return {
        "market_id": market_id,
        "platform": "polymarket",
        "question": question,
        "category": category,
        "outcome": outcome,
        "volume_usd": volume,
        "close_price": close_price,
        "source": "seed",  # tag so we can distinguish from real trades
    }


def parse_metaculus(m: dict) -> dict | None:
    """Parse a Metaculus resolved market into a seed episode."""
    question = m.get("title") or m.get("question", "")
    if not question:
        return None

    resolution = m.get("resolution")
    if resolution is None:
        return None

    try:
        res_float = float(resolution)
    except (ValueError, TypeError):
        return None

    # Metaculus resolves as 0.0 or 1.0 for binary questions
    if res_float >= 0.95:
        outcome = "Yes"
    elif res_float <= 0.05:
        outcome = "No"
    else:
        return None  # skip partial resolutions (continuous questions)

    return {
        "market_id": str(m.get("id", "")),
        "platform": "metaculus",
        "question": question,
        "category": (m.get("category") or m.get("group", "other")).lower().strip(),
        "outcome": outcome,
        "community_prediction": m.get("community_prediction"),
        "source": "seed",
    }


def parse_manifold(m: dict) -> dict | None:
    """Parse a Manifold resolved market into a seed episode."""
    question = m.get("question", "")
    if not question:
        return None

    resolution = m.get("resolution")
    if not resolution:
        return None

    res_lower = str(resolution).lower()
    if res_lower in ("yes", "true"):
        outcome = "Yes"
    elif res_lower in ("no", "false"):
        outcome = "No"
    else:
        return None  # skip MKT, CANCEL, etc.

    return {
        "market_id": str(m.get("id", "")),
        "platform": "manifold",
        "question": question,
        "category": (m.get("groupSlugs", ["other"])[0] if m.get("groupSlugs") else "other").lower(),
        "outcome": outcome,
        "close_probability": m.get("closeProb") or m.get("probability"),
        "volume_usd": float(m.get("volume") or 0),
        "source": "seed",
    }


def parse_market(m: dict) -> dict | None:
    """Auto-detect format and parse a resolved market."""
    # Detect by platform field if present
    platform = (m.get("platform") or "").lower()
    if platform == "metaculus":
        return parse_metaculus(m)
    if platform == "manifold":
        return parse_manifold(m)
    if platform == "polymarket":
        return parse_polymarket(m)

    # Heuristic detection
    if "condition_id" in m or "conditionId" in m or "outcomePrices" in m:
        return parse_polymarket(m)
    if "community_prediction" in m or m.get("type") == "binary":
        return parse_metaculus(m)
    if "groupSlugs" in m or "closeProb" in m:
        return parse_manifold(m)

    # Default: try Polymarket (most common in our data)
    return parse_polymarket(m)


# ---------------------------------------------------------------------------
# Seeding logic
# ---------------------------------------------------------------------------

def load_resolved_markets(filepath: Path) -> list[dict]:
    """Load resolved markets from JSON file. Handles both list and dict formats."""
    logger.info(f"Loading resolved markets from {filepath}")
    raw = json.loads(filepath.read_text(encoding="utf-8"))

    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        # Common wrapper formats
        return (
            raw.get("markets", [])
            or raw.get("data", [])
            or raw.get("resolved", [])
            or raw.get("results", [])
        )
    return []


def seed(
    filepath: Path,
    dry_run: bool = False,
    limit: int | None = None,
    platform_filter: str | None = None,
    batch_size: int = 50,
) -> dict:
    """Seed episodic memory from resolved markets file.

    Returns stats dict with counts.
    """
    markets = load_resolved_markets(filepath)
    logger.info(f"Loaded {len(markets)} raw market records")

    if limit:
        markets = markets[:limit]
        logger.info(f"Limited to {limit} markets")

    # Parse into seed episodes
    episodes: list[dict] = []
    skipped = 0
    for m in markets:
        ep = parse_market(m)
        if ep is None:
            skipped += 1
            continue
        if platform_filter and ep.get("platform") != platform_filter:
            skipped += 1
            continue
        episodes.append(ep)

    logger.info(f"Parsed {len(episodes)} valid episodes ({skipped} skipped)")

    if dry_run:
        # Show sample and category breakdown
        cats: dict[str, int] = {}
        platforms: dict[str, int] = {}
        outcomes: dict[str, int] = {}
        for ep in episodes:
            cats[ep.get("category", "?")] = cats.get(ep.get("category", "?"), 0) + 1
            platforms[ep.get("platform", "?")] = platforms.get(ep.get("platform", "?"), 0) + 1
            outcomes[ep.get("outcome", "?")] = outcomes.get(ep.get("outcome", "?"), 0) + 1

        print(f"\n{'='*60}")
        print(f"DRY RUN — would seed {len(episodes)} episodes")
        print(f"{'='*60}")
        print(f"\nPlatforms: {json.dumps(platforms, indent=2)}")
        print(f"\nOutcomes:  {json.dumps(outcomes, indent=2)}")
        print(f"\nTop categories:")
        for cat, count in sorted(cats.items(), key=lambda x: -x[1])[:15]:
            print(f"  {cat}: {count}")
        print(f"\nSample episode:")
        print(json.dumps(episodes[0], indent=2, default=str))
        return {"parsed": len(episodes), "seeded": 0, "skipped": skipped, "dry_run": True}

    # Seed episodic memory
    episodic = EpisodicMemory()
    existing_count = episodic.count()
    logger.info(f"Episodic memory currently has {existing_count} episodes")

    seeded = 0
    errors = 0
    start = time.time()

    for i, ep in enumerate(episodes):
        try:
            # Use a stable ID so re-running doesn't create duplicates
            ep["id"] = f"seed_{ep['platform']}_{ep['market_id']}"
            episodic.record(ep)
            seeded += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                logger.warning(f"Failed to seed episode: {e}")

        # Progress reporting
        if (i + 1) % batch_size == 0:
            elapsed = time.time() - start
            rate = (i + 1) / elapsed
            remaining = (len(episodes) - i - 1) / rate if rate > 0 else 0
            print(
                f"  Progress: {i + 1}/{len(episodes)} "
                f"({rate:.0f}/sec, ~{remaining:.0f}s remaining)",
                end="\r",
            )

    elapsed = time.time() - start
    print(f"\n\nSeeded {seeded} episodes in {elapsed:.1f}s ({errors} errors)")

    final_count = episodic.count()
    logger.info(
        f"Episodic memory: {existing_count} -> {final_count} episodes "
        f"(+{final_count - existing_count} net new)"
    )

    return {
        "parsed": len(episodes),
        "seeded": seeded,
        "errors": errors,
        "skipped": skipped,
        "elapsed_seconds": round(elapsed, 1),
        "existing_before": existing_count,
        "total_after": final_count,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Seed episodic memory from resolved markets data"
    )
    parser.add_argument(
        "file",
        nargs="?",
        default=str(DEFAULT_FILE),
        help=f"Path to resolved markets JSON (default: {DEFAULT_FILE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be seeded without writing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit to N markets (useful for testing)",
    )
    parser.add_argument(
        "--platform",
        choices=["polymarket", "metaculus", "manifold"],
        default=None,
        help="Filter to a specific platform",
    )

    args = parser.parse_args()
    filepath = Path(args.file)

    if not filepath.exists():
        print(f"Error: file not found: {filepath}")
        print(f"\nExpected resolved markets at: {DEFAULT_FILE}")
        print("Make sure the Linux box has synced data to DATA_PATH/linux_shared/")
        print("\nOr provide a path directly:")
        print("  python scripts/seed_memory.py /path/to/resolved_markets.json")
        sys.exit(1)

    stats = seed(
        filepath=filepath,
        dry_run=args.dry_run,
        limit=args.limit,
        platform_filter=args.platform,
    )

    print(f"\nResults: {json.dumps(stats, indent=2)}")

    if not args.dry_run and stats["seeded"] > 0:
        from core.strategy_params import SP
        bt_min = SP.bt_min_sample
        print(f"\nBacktester requires {bt_min} similar resolved markets to pass.")
        if stats["total_after"] >= bt_min:
            print("Bootstrap mode should now be bypassed for most markets.")
        else:
            print(
                f"Only {stats['total_after']} episodes — may still enter bootstrap "
                f"for categories with sparse data."
            )


if __name__ == "__main__":
    main()
