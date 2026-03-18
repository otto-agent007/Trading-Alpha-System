from __future__ import annotations

import logging

from clients.base import MarketClient
from core.memory.episodic import EpisodicMemory
from core.memory.working import WorkingMemory
from core.models import Market, TradeDecision

logger = logging.getLogger(__name__)


class Executor:
    """Executes trade decisions (paper or live).

    For now, always paper mode — logs the decision to episodic memory
    and tracks a virtual position in working memory.
    """

    def __init__(
        self,
        clients: dict[str, MarketClient],
        episodic: EpisodicMemory,
        working: WorkingMemory,
    ) -> None:
        self._clients = clients
        self._episodic = episodic
        self._working = working

    def execute(self, decision: TradeDecision, market: Market) -> bool:
        """Execute a trade decision. Returns True if executed successfully."""
        if decision.action == "pass":
            self._log_episode(decision, market, executed=False)
            return False

        if decision.paper_only:
            return self._paper_execute(decision, market)
        else:
            return self._live_execute(decision, market)

    def _paper_execute(self, decision: TradeDecision, market: Market) -> bool:
        """Paper trade: record position in working memory, log to episodic."""
        self._working.record_position(decision, market)
        self._log_episode(decision, market, executed=True)

        logger.info(
            f"Executor [PAPER]: {decision.action} {market.question[:50]} "
            f"@ {decision.target_price:.3f} (${decision.size_usd:.2f})"
        )
        return True

    def _live_execute(self, decision: TradeDecision, market: Market) -> bool:
        """Live trade: place order via platform client.

        Not implemented yet — Phase 5.
        """
        logger.warning("Executor: live trading not implemented yet (Phase 5)")
        return self._paper_execute(decision, market)

    def _log_episode(self, decision: TradeDecision, market: Market, executed: bool) -> None:
        """Record the decision in episodic memory."""
        self._episodic.record({
            "market_id": decision.market_id,
            "platform": decision.platform,
            "question": market.question,
            "category": market.category,
            "action": decision.action,
            "target_price": decision.target_price,
            "size_usd": decision.size_usd,
            "kelly_fraction": decision.kelly_fraction,
            "backtest_ev": decision.backtest_ev,
            "backtest_sample": decision.backtest_sample,
            "reasoning": decision.reasoning[:500],
            "executed": executed,
            "paper_only": decision.paper_only,
            "outcome": "",  # filled in later by reviewer
        })
