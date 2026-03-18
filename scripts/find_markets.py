"""
Interactive market search and watchlist seeding tool.

Usage:
    python scripts/find_markets.py                  # interactive mode
    python scripts/find_markets.py "BTC 5-minute"   # search directly
    python scripts/find_markets.py --auto            # auto-add from WATCH_KEYWORDS
"""
from __future__ import annotations

import os
import sys

# allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
import re
from datetime import datetime, timezone

CLOB = "https://clob.polymarket.com"


# ── helpers ──────────────────────────────────────────────────────────────────

def fetch_markets(keyword: str, max_pages: int = 10, limit: int = 100) -> list[dict]:
    """Scan CLOB API pages for markets matching keyword (case-insensitive)."""
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    matches: list[dict] = []
    cursor = None

    for page_num in range(max_pages):
        params: dict = {"limit": limit, "active": "true"}
        if cursor:
            params["next_cursor"] = cursor

        try:
            r = httpx.get(f"{CLOB}/markets", params=params, timeout=15)
            r.raise_for_status()
            resp = r.json()
        except Exception as e:
            print(f"  [page {page_num + 1}] API error: {e}")
            break

        data = resp.get("data", [])
        for m in data:
            question = m.get("question", "")
            description = m.get("description", "")
            if pattern.search(question) or pattern.search(description):
                matches.append(m)

        cursor = resp.get("next_cursor")
        if not cursor or cursor in ("", "LTE="):
            break

        # show progress
        print(f"  [page {page_num + 1}] scanned {len(data)} markets, {len(matches)} matches so far...", end="\r")

    print(f"  Scanned {min((page_num + 1), max_pages)} pages, found {len(matches)} matches.          ")
    return matches


def format_market(m: dict, idx: int) -> str:
    """Pretty-print one market for the interactive picker."""
    question = m.get("question", "???")[:90]
    tokens = m.get("tokens", [])
    yes_price = "?"
    for tok in tokens:
        if tok.get("outcome", "").lower() == "yes":
            yes_price = tok.get("price", "?")
            break
    vol = float(m.get("volume_num", m.get("volume", 0)) or 0)
    cid = str(m.get("condition_id", m.get("conditionId", "")))[:16]
    end = m.get("end_date_iso", "")[:16] if m.get("end_date_iso") else "no date"
    return f"  [{idx}] {question}\n      YES: {yes_price}  |  Vol: ${vol:,.0f}  |  Closes: {end}  |  id: {cid}..."


def add_to_watchlist(markets_to_add: list[dict]) -> None:
    """Add selected markets to working memory watchlist."""
    from clients.polymarket import PolymarketClient
    from core.memory.working import WorkingMemory

    client = PolymarketClient()
    wm = WorkingMemory.load()

    for m in markets_to_add:
        parsed = client._parse_clob_market(m)
        wm.add_to_watchlist(parsed, reason="manual pick", pattern_score=0.5)
        print(f"  + {parsed.question[:70]}")

    print(f"\nWatchlist now has {len(wm.watchlist)} items.")


# ── modes ────────────────────────────────────────────────────────────────────

def search_and_pick(keyword: str) -> None:
    """Search for a keyword, show results, let user pick which to add."""
    print(f"\nSearching for: \"{keyword}\"\n")
    matches = fetch_markets(keyword)

    if not matches:
        print("  No matching markets found.\n")
        return

    print()
    for i, m in enumerate(matches, 1):
        print(format_market(m, i))
    print()

    selection = input("Add which? (e.g. 1,3,5 or 'all' or 'none'): ").strip().lower()
    if selection in ("none", "", "n"):
        return
    if selection == "all":
        add_to_watchlist(matches)
        return

    try:
        indices = [int(x.strip()) - 1 for x in selection.split(",")]
        selected = [matches[i] for i in indices if 0 <= i < len(matches)]
        if selected:
            add_to_watchlist(selected)
        else:
            print("No valid selections.")
    except ValueError:
        print("Invalid input. Use comma-separated numbers.")


def interactive_mode() -> None:
    """Loop: search, pick, repeat."""
    print("=" * 60)
    print("  Polymarket Market Finder")
    print("  Type a search term, or 'quit' to exit.")
    print("  Searches market questions on Polymarket CLOB API.")
    print("=" * 60)

    while True:
        try:
            query = input("\nSearch: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if query.lower() in ("quit", "exit", "q"):
            break
        if not query:
            continue
        search_and_pick(query)

    # show final watchlist
    try:
        from core.memory.working import WorkingMemory
        wm = WorkingMemory.load()
        if wm.watchlist:
            print(f"\nCurrent watchlist ({len(wm.watchlist)} items):")
            for w in wm.watchlist:
                print(f"  - {w.question[:70]} [{w.platform}]")
    except Exception:
        pass


def auto_mode() -> None:
    """Auto-search using WATCH_KEYWORDS from config/env and add all matches."""
    raw = os.getenv("WATCH_KEYWORDS", "")
    if not raw:
        print("No WATCH_KEYWORDS set in .env. Add e.g.:")
        print('  WATCH_KEYWORDS=BTC 5-Minute,BTC 15-Minute,NBA')
        sys.exit(1)

    keywords = [k.strip() for k in raw.split(",") if k.strip()]
    print(f"Auto-discovery with {len(keywords)} keywords: {keywords}\n")

    all_matches: list[dict] = []
    seen_ids: set[str] = set()

    for kw in keywords:
        print(f"--- {kw} ---")
        matches = fetch_markets(kw, max_pages=5)
        for m in matches:
            cid = m.get("condition_id", m.get("conditionId", ""))
            if cid not in seen_ids:
                seen_ids.add(cid)
                all_matches.append(m)

    if all_matches:
        print(f"\nFound {len(all_matches)} unique markets:")
        for i, m in enumerate(all_matches, 1):
            print(format_market(m, i))
        print()
        confirm = input(f"Add all {len(all_matches)} to watchlist? (y/n): ").strip().lower()
        if confirm in ("y", "yes"):
            add_to_watchlist(all_matches)
        else:
            print("Aborted.")
    else:
        print("No markets found for any keyword.")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) > 1:
        if sys.argv[1] == "--auto":
            auto_mode()
        else:
            keyword = " ".join(sys.argv[1:])
            search_and_pick(keyword)
    else:
        interactive_mode()


if __name__ == "__main__":
    main()
