from __future__ import annotations

import logging
from datetime import datetime, timezone

from clients.base import MarketClient
from core.memory.consolidation import consolidate
from core.memory.episodic import EpisodicMemory
from core.memory.semantic import SemanticMemory
from core.memory.working import WorkingMemory
from core.router import ModelRouter

logger = logging.getLogger(__name__)


class Reviewer:
    """Daily review agent — checks outcomes, updates PnL, triggers learning.

    Runs at 03:00 UTC. Resolves closed positions, updates working memory,
    runs consolidation to extract new semantic patterns.
    """

    def __init__(
        self,
        clients: dict[str, MarketClient],
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

    def run(self) -> dict:
        """Full review cycle. Returns summary stats."""
        logger.info("Reviewer: starting daily review...")

        resolved_count = self._check_positions()
        self._update_open_prices()

        # Run consolidation (episodic → semantic learning extraction)
        consolidate(self._episodic, self._semantic, self._router)

        stats = {
            "resolved_positions": resolved_count,
            "open_positions": len(self._working.open_positions()),
            "total_exposure": self._working.total_exposure(),
            "bankroll": self._working.bankroll,
            "learned_patterns": self._semantic.count(),
            "total_episodes": self._episodic.count(),
        }

        logger.info(
            f"Reviewer: done — resolved={resolved_count}, "
            f"open={stats['open_positions']}, bankroll=${stats['bankroll']:.2f}"
        )
        return stats

    def _check_positions(self) -> int:
        """Check if any open positions have resolved."""
        resolved_count = 0
        for pos in self._working.open_positions():
            client = self._clients.get(pos.platform)
            if not client:
                continue

            try:
                market = client.get_market(pos.market_id)
            except Exception as e:
                logger.debug(f"Reviewer: couldn't fetch {pos.market_id}: {e}")
                continue

            if market.status == "resolved" and market.resolved_outcome:
                pnl = self._working.resolve_position(
                    pos.market_id, market.resolved_outcome
                )

                # Record outcome in episodic memory
                self._episodic.record({
                    "market_id": pos.market_id,
                    "platform": pos.platform,
                    "question": pos.question,
                    "category": market.category,
                    "action": f"buy_{pos.direction}",
                    "entry_price": pos.entry_price,
                    "outcome": market.resolved_outcome,
                    "pnl": pnl,
                    "resolved": True,
                })
                resolved_count += 1

        return resolved_count

    def _update_open_prices(self) -> None:
        """Update current prices on open positions."""
        for pos in self._working.open_positions():
            client = self._clients.get(pos.platform)
            if not client:
                continue
            try:
                market = client.get_market(pos.market_id)
                if pos.direction == "yes":
                    pos.current_price = market.current_prices.get("Yes", pos.current_price)
                else:
                    pos.current_price = market.current_prices.get("No", pos.current_price)
            except Exception as e:
                logger.debug(f"Reviewer: price update failed for {pos.market_id}: {e}")
        self._working.save()
