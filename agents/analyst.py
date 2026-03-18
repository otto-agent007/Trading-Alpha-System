from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from clients.base import MarketClient
from core.memory.episodic import EpisodicMemory
from core.memory.semantic import SemanticMemory
from core.memory.working import WorkingMemory
from core.models import Market, MarketAnalysis, WatchlistItem
from core.linux_handoff import LINUX
from core.router import ModelRouter
from core.strategy_params import SP

logger = logging.getLogger(__name__)

_YEAR_RE = re.compile(r"\b(20[2-9]\d)\b")


class Analyst:
    """Deep-dives into a watchlisted market using the heavy LLM.

    Gathers orderbook, price history, and memory context, then asks
    the LLM to estimate fair value and explain its reasoning.
    """

    def __init__(
        self,
        clients: dict[str, MarketClient],  # {"polymarket": ..., "kalshi": ...}
        router: ModelRouter,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        working: WorkingMemory,
    ) -> None:
        self._clients = clients
        self._router = router
        self._episodic = episodic
        self._semantic = semantic
        self._working = working

    def analyze_next(self) -> MarketAnalysis | None:
        """Pick the top watchlisted market and produce a MarketAnalysis."""
        top = self._working.get_top_watchlist(n=1)
        if not top:
            logger.info("Analyst: no markets on watchlist to analyze")
            return None
        return self.analyze(top[0])

    def analyze(self, item: WatchlistItem) -> MarketAnalysis | None:
        """Full analysis of a specific watchlisted market."""
        # Guard: skip empty market IDs
        if not item.market_id:
            logger.warning("Analyst: skipping item with empty market_id")
            return None

        # Guard: skip markets referencing past years (saves OpenRouter credits)
        year_match = _YEAR_RE.search(item.question)
        if year_match:
            ref_year = int(year_match.group(1))
            current_year = datetime.now(timezone.utc).year
            if ref_year < current_year:
                logger.info(
                    f"Analyst: skipping past-year market ({ref_year}): {item.question[:60]}"
                )
                return None

        client = self._clients.get(item.platform)
        if not client:
            logger.error(f"Analyst: no client for platform {item.platform}")
            return None

        try:
            market = client.get_market(item.market_id)
        except Exception as e:
            logger.error(f"Analyst: failed to fetch market {item.market_id}: {e}")
            return None

        # Guard: skip closed/resolved markets (safety net — scanner may have missed these)
        if market.status != "open":
            logger.info(
                f"Analyst: skipping {market.status} market: {market.question[:60]}"
            )
            return None

        # Guard: skip if close_date has already passed
        if market.close_date and (market.close_date - datetime.now(timezone.utc)).total_seconds() < 0:
            logger.info(
                f"Analyst: skipping expired market (closed {market.close_date}): "
                f"{market.question[:60]}"
            )
            return None

        # Gather context
        orderbook = self._safe_call(client.get_orderbook, item.market_id)
        price_df = self._safe_call(client.get_price_history, item.market_id, "1h")
        trades = self._safe_call(client.get_trades, item.market_id, 20)

        # Memory queries
        similar_episodes = self._episodic.recall(f"market: {market.question}", n=5)
        applicable_patterns = self._semantic.query_patterns(
            f"{market.category}: {market.question}", n=5
        )

        # Build orderbook summary
        ob_summary = "No orderbook data"
        if orderbook:
            ob_summary = (
                f"Spread: {orderbook.spread:.3f}, Mid: {orderbook.mid_price:.3f}, "
                f"Bids: {len(orderbook.bids)} levels, Asks: {len(orderbook.asks)} levels"
            )

        # Build price trend summary
        price_summary = "No price history"
        if price_df is not None and not price_df.empty:
            recent = price_df.tail(24)  # last 24 candles
            if not recent.empty:
                start_price = recent.iloc[0]["close"]
                end_price = recent.iloc[-1]["close"]
                high = recent["high"].max()
                low = recent["low"].min()
                change_pct = ((end_price - start_price) / start_price * 100) if start_price else 0
                price_summary = (
                    f"24h trend: {start_price:.3f} → {end_price:.3f} "
                    f"({change_pct:+.1f}%), range {low:.3f}-{high:.3f}"
                )

        # Format memory context
        past_market_summaries = [
            f"- {ep.get('question', ep.get('market_id', '?'))[:60]}: "
            f"action={ep.get('action', '?')}, outcome={ep.get('outcome', 'pending')}"
            for ep in similar_episodes[:5]
        ]
        pattern_summaries = [
            f"- [{p.get('confidence', '?')}] {p.get('pattern', '?')[:80]}"
            for p in applicable_patterns[:5]
        ]

        current_yes_price = market.current_prices.get("Yes", 0.5)

        # Build list sections before the f-string (can't use \n inside f-string expressions)
        past_markets_text = "\n".join(past_market_summaries) if past_market_summaries else "None found"
        patterns_text = "\n".join(pattern_summaries) if pattern_summaries else "None learned yet"

        # Statistical patterns from research factory Track 2
        stat_edges = LINUX.get_statistical_edges(market.category)
        stat_text = "No statistical patterns available"
        if stat_edges:
            lines = []
            for p in stat_edges[:3]:
                h = p.get("hypothesis", {})
                lines.append(
                    f"- {h.get('type', '?')} ({h.get('category', '?')}): "
                    f"edge={p.get('edge', 0):+.1%}, z={p.get('z_score', 0):.1f}, n={p.get('n', 0)}"
                )
            stat_text = "\n".join(lines)

        # Build the prompt
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        prompt = (
            f"## Market Analysis Request\n\n"
            f"**Current date/time:** {now_str}\n"
            f"**Question:** {market.question}\n"
            f"**Description:** {market.description[:500]}\n"
            f"**Platform:** {market.platform}\n"
            f"**Category:** {market.category}\n"
            f"**Current YES price:** {current_yes_price:.3f}\n"
            f"**Volume:** ${market.volume_usd:,.0f}\n"
            f"**Orderbook:** {ob_summary}\n"
            f"**Price trend:** {price_summary}\n\n"
            f"**Similar past markets:**\n"
            f"{past_markets_text}\n\n"
            f"**Known patterns for this category:**\n"
            f"{patterns_text}\n\n"
            f"**Statistical patterns (data-driven, from {len(stat_edges)} analyses):**\n"
            f"{stat_text}\n\n"
            f"**Important:** If this market's resolution depends on a prior undetermined event "
            f"(e.g. a specific playoff matchup that hasn't been set yet, an election outcome, "
            f"a team qualifying for a championship, or any upstream condition that is still uncertain), "
            f"set confidence to 0.25 or lower. Do not estimate fair_value as though that condition is already known.\n\n"
            f"Based on ALL the above context:\n"
            f"1. What is the fair probability that YES resolves? (0.0 to 1.0)\n"
            f"2. How confident are you in this estimate? (0.0 to 1.0)\n"
            f"3. What is your reasoning? (2-4 sentences)\n\n"
            f"Return JSON: {{\"fair_value\": float, \"confidence\": float, \"reasoning\": string}}"
        )

        # Use winning prompt from Track 3 if available, else default
        best_prompt = LINUX.get_best_analyst_prompt()
        system_prompt = best_prompt or (
            "You are a quantitative prediction market analyst. "
            "Estimate fair probabilities based on evidence, not sentiment. "
            "Be calibrated — state low confidence when evidence is weak."
        )
        feedback = LINUX.get_human_feedback()
        if feedback.get("analyst_notes"):
            notes = "\n".join(f"- {n}" for n in feedback["analyst_notes"])
            system_prompt += f"\n\nHuman analyst notes:\n{notes}"

        try:
            raw = self._router.reason(
                prompt,
                system=system_prompt,
                temperature=0.4,
            )
            data = json.loads(raw)
            # Clamp fair_value to a valid probability range — prevents extreme outputs
            # from distorting edge calculations
            fair_value = max(SP.price_floor, min(SP.price_ceiling, float(data.get("fair_value", current_yes_price))))
            confidence = float(data.get("confidence", 0.3))
            reasoning = data.get("reasoning", "No reasoning provided")
        except Exception as e:
            logger.error(f"Analyst: LLM analysis failed for {item.market_id}: {e}")
            return None

        edge = fair_value - current_yes_price

        # Sanity check: large edge claimed against a liquid mid-range market is suspicious.
        # The current market price reflects collective intelligence — a 30+ cent deviation
        # usually signals model confusion, not genuine alpha.
        if abs(edge) > SP.suspicious_edge and 0.30 <= current_yes_price <= 0.70:
            original_conf = confidence
            confidence = min(confidence, SP.confidence_cap_on_suspicious)
            logger.warning(
                f"Analyst: large edge ({edge:+.3f}) against mid-range market "
                f"({current_yes_price:.3f}) — capping confidence {original_conf:.2f} → {confidence:.2f}"
            )

        analysis = MarketAnalysis(
            market_id=market.id,
            platform=market.platform,
            question=market.question,
            current_price=current_yes_price,
            estimated_fair_value=fair_value,
            edge=edge,
            confidence=confidence,
            reasoning=reasoning,
            orderbook_summary=ob_summary,
            similar_past_markets=[ep.get("market_id", "") for ep in similar_episodes[:5]],
            applicable_patterns=[p.get("pattern", "") for p in applicable_patterns[:5]],
        )

        logger.info(
            f"Analyst: {market.question[:50]} → fair={fair_value:.3f} vs current={current_yes_price:.3f} "
            f"(edge={edge:+.3f}, conf={confidence:.2f})"
        )

        return analysis

    @staticmethod
    def _safe_call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            logger.debug(f"Analyst: call to {fn.__name__} failed: {e}")
            return None
