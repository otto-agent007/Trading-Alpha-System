from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from clients.base import MarketClient
from config import WATCH_KEYWORDS
from core.memory.semantic import SemanticMemory
from core.memory.working import WorkingMemory
from core.models import Market
from core.linux_handoff import LINUX
from core.router import ModelRouter
from core.strategy_params import SP

logger = logging.getLogger(__name__)

# Regex to detect 4-digit year references (2020-2099)
_YEAR_RE = re.compile(r"\b(20[2-9]\d)\b")


class Scanner:
    """Discovers interesting markets across platforms.

    Three-tier discovery:
      1. Keyword match (WATCH_KEYWORDS from .env) — no LLM needed
      2. Semantic memory pattern match — no LLM needed
      3. High-volume mid-range price heuristic — no LLM needed
      4. LLM classification (if Ollama available) — optional bonus

    This means the scanner always works, even without Ollama running.
    """

    def __init__(
        self,
        clients: list[MarketClient],
        router: ModelRouter,
        semantic: SemanticMemory,
        working: WorkingMemory,
    ) -> None:
        self._clients = clients
        self._router = router
        self._semantic = semantic
        self._working = working
        # Pre-compile keyword patterns for fast matching
        # Word boundaries prevent substring false positives (e.g. "NBA" in unrelated text)
        self._keyword_patterns = [
            (kw, re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))
            for kw in WATCH_KEYWORDS
        ]
        self._skip_cats: set[str] = set()  # populated from human feedback at run time

    def run(self) -> dict:
        """Scan all platforms. Returns scan statistics dict."""
        existing_ids = {w.market_id for w in self._working.watchlist}

        # Human feedback — category skips and forced analyses
        feedback = LINUX.get_human_feedback()
        self._skip_cats = set(feedback.get("skip_categories", []))
        if self._skip_cats:
            logger.info(f"Scanner: human feedback skipping categories: {self._skip_cats}")
        for market_id in feedback.get("force_analyze", []):
            if market_id not in existing_ids:
                logger.info(f"Scanner: human feedback forcing analysis of {market_id}")
                if market_id not in self._working.pending_analyses:
                    self._working.pending_analyses.append(market_id)

        totals = {
            "added": 0, "total_scanned": 0, "keyword_hits": 0,
            "keyword_filtered": 0, "heuristic_hits": 0,
        }

        for client in self._clients:
            try:
                platform_stats = self._scan_platform(client, existing_ids)
                for key in totals:
                    totals[key] += platform_stats[key]
            except Exception as e:
                logger.error(f"Scanner: platform scan failed: {e}")

        # Clean up: remove resolved/closed markets from watchlist
        self._cleanup_watchlist()

        self._working.last_scan = datetime.now(timezone.utc)
        self._working.save()

        totals["watchlist_size"] = len(self._working.watchlist)
        logger.info(f"Scanner: added {totals['added']} markets to watchlist")
        return totals

    def _scan_platform(self, client: MarketClient, existing_ids: set[str]) -> dict:
        stats = {
            "added": 0, "total_scanned": 0, "keyword_hits": 0,
            "keyword_filtered": 0, "heuristic_hits": 0,
        }
        cursor = None
        has_keywords = bool(self._keyword_patterns)
        # Scan more pages when we have keywords to search for
        max_pages = 20 if has_keywords else 5

        for page_num in range(max_pages):
            page = client.list_markets(active=True, limit=100, cursor=cursor)
            stats["total_scanned"] += len(page.markets)

            for market in page.markets:
                if market.id in existing_ids:
                    continue

                # Check keywords on ALL pages — keyword matches bypass volume/price filters
                # but must still pass _is_alive() to avoid dead/old markets
                keyword_match = self._check_keywords(market)
                if keyword_match:
                    if not self._is_alive(market):
                        stats["keyword_filtered"] += 1
                        yes_price = market.current_prices.get("Yes", 0.5)
                        logger.info(
                            f"Scanner: keyword hit FILTERED — {market.question[:80]!r} "
                            f"(status={market.status}, close_date={market.close_date}, price={yes_price:.3f})"
                        )
                        continue
                    reason, score = keyword_match
                    self._working.add_to_watchlist(market, reason, score)
                    existing_ids.add(market.id)
                    stats["added"] += 1
                    stats["keyword_hits"] += 1
                    logger.info(f"Scanner: keyword hit — {market.question[:60]}")
                    continue

                # Heuristic/semantic discovery only on first 5 pages
                if page_num >= 5:
                    continue

                if not self._passes_basic_filters(market):
                    continue

                reason, score = self._evaluate(market)
                if reason:
                    self._working.add_to_watchlist(market, reason, score)
                    existing_ids.add(market.id)
                    stats["added"] += 1
                    stats["heuristic_hits"] += 1

            if not page.next_cursor:
                break
            cursor = page.next_cursor

        logger.info(f"Scanner: scanned {stats['total_scanned']} markets across {page_num + 1} pages")
        return stats

    def _check_keywords(self, market: Market) -> tuple[str, float] | None:
        """Check if market question matches any WATCH_KEYWORDS. Returns (reason, score) or None.

        Only searches the question field to avoid false positives from long descriptions.
        """
        for kw, pattern in self._keyword_patterns:
            match = pattern.search(market.question)
            if match:
                logger.info(
                    f"Scanner: keyword '{kw}' matched at pos {match.start()} "
                    f"in full question: {market.question!r}"
                )
                return f"Keyword match: \"{kw}\"", 0.7
        return None

    def _is_alive(self, market: Market) -> bool:
        """Lightweight liveness check: status, close date, year reference, and price extremes.

        Applied to ALL markets including keyword matches.
        Does NOT check volume or max-days-to-close — keyword matches bypass those.
        """
        if market.status != "open":
            return False
        if not market.id:
            return False
        if market.close_date:
            now = datetime.now(timezone.utc)
            if (market.close_date - now).total_seconds() < 0:
                return False
        # Catch markets referencing past years (e.g. "2024 NBA Championship" in 2026)
        year_match = _YEAR_RE.search(market.question)
        if year_match:
            ref_year = int(year_match.group(1))
            current_year = datetime.now(timezone.utc).year
            if ref_year < current_year:
                return False
        # Reject effectively-resolved markets (price at extremes applies to ALL markets,
        # including keyword matches — a 0.01 market is done regardless of keyword hit)
        yes_price = market.current_prices.get("Yes", 0.5)
        if yes_price < 0.05 or yes_price > 0.95:
            return False
        return True

    def _passes_basic_filters(self, market: Market) -> bool:
        """Quick filters: liveness, volume, time horizon, price range."""
        if not self._is_alive(market):
            return False
        if market.category in self._skip_cats:
            return False
        if market.volume_usd < SP.min_volume_usd:
            return False
        if market.close_date:
            now = datetime.now(timezone.utc)
            days_left = (market.close_date - now).days
            if days_left > SP.max_days_to_close:
                return False
        # Skip markets at extreme prices (likely already resolved)
        yes_price = market.current_prices.get("Yes", 0.5)
        if yes_price < SP.price_floor or yes_price > SP.price_ceiling:
            return False
        return True

    def _evaluate(self, market: Market) -> tuple[str, float]:
        """Score a market using semantic memory, volume heuristic, or LLM.

        Returns (reason, score) or ("", 0) if not interesting.
        Keyword matching is handled separately in _check_keywords() and runs
        before basic filters. This method handles tiers 2-4.
        """
        # ── Tier 2: Semantic memory pattern match ──
        try:
            patterns = self._semantic.query_patterns(market.question, n=3)
            if patterns:
                best = max(patterns, key=lambda p: float(p.get("confidence", 0)))
                conf = float(best.get("confidence", 0))
                if conf > 0.5:
                    reason = f"Pattern match ({conf:.0%}): {best.get('pattern', '')[:80]}"
                    return reason, conf
        except Exception as e:
            logger.debug(f"Semantic memory query failed: {e}")

        # ── Tier 3: High-volume mid-range heuristic ──
        yes_price = market.current_prices.get("Yes", 0.5)
        if market.volume_usd > 50_000 and 0.20 < yes_price < 0.80:
            return f"High volume (${market.volume_usd:,.0f}) mid-range price", 0.3

        # ── Tier 4: LLM classification (optional, if Ollama is available) ──
        try:
            category = self._router.classify(
                f"Prediction market: {market.question}",
                ["crypto", "politics", "sports", "economics", "other"],
            )
            # If classification works, check semantic memory with category context
            patterns = self._semantic.query_patterns(
                f"{category}: {market.question}", n=3
            )
            if patterns:
                best = max(patterns, key=lambda p: float(p.get("confidence", 0)))
                conf = float(best.get("confidence", 0))
                if conf > 0.4:
                    return f"LLM+pattern ({category}, {conf:.0%})", conf
        except Exception:
            # Ollama not available — that's fine, tiers 1-3 handle discovery
            pass

        return "", 0.0

    def _cleanup_watchlist(self) -> None:
        """Remove markets that are no longer active."""
        to_remove: list[str] = []
        now = datetime.now(timezone.utc)

        for item in self._working.watchlist:
            if item.added_at and (now - item.added_at).days > 7:
                to_remove.append(item.market_id)
                continue

        for mid in to_remove:
            self._working.remove_from_watchlist(mid)

        if to_remove:
            logger.info(f"Scanner: cleaned up {len(to_remove)} stale watchlist entries")
