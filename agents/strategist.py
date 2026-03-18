from __future__ import annotations

import logging

from core.linux_handoff import LINUX
from core.memory.working import WorkingMemory
from core.models import BacktestResult, MarketAnalysis, TradeDecision
from core.router import ModelRouter
from core.strategy_params import SP

logger = logging.getLogger(__name__)


class Strategist:
    """Decides whether to trade based on analysis + backtest results.

    The decision is fully mechanical (edge, confidence, Kelly sizing).
    All thresholds are read from SP (core/strategy_params.py) so they
    can be tuned via params.json without code changes.
    """

    def __init__(
        self,
        router: ModelRouter,
        working: WorkingMemory,
    ) -> None:
        self._router = router
        self._working = working

    def decide(
        self,
        analysis: MarketAnalysis,
        backtest: BacktestResult,
    ) -> TradeDecision:
        """Produce a TradeDecision given analysis + passing backtest."""
        # Quick mechanical checks before involving the LLM
        if not backtest.passed:
            return self._pass_decision(analysis, backtest, "Backtest did not pass")

        if abs(analysis.edge) < SP.min_edge:
            return self._pass_decision(
                analysis, backtest, f"Edge {analysis.edge:.4f} below minimum {SP.min_edge}"
            )

        if analysis.confidence < SP.min_confidence:
            return self._pass_decision(
                analysis, backtest,
                f"Confidence {analysis.confidence:.2f} below minimum {SP.min_confidence}",
            )

        # Reject implausibly large edges against liquid mid-range markets
        if abs(analysis.edge) > SP.max_edge and 0.25 <= analysis.current_price <= 0.75:
            return self._pass_decision(
                analysis, backtest,
                f"Edge {analysis.edge:+.3f} implausibly large for "
                f"a {analysis.current_price:.3f}-priced market (max_edge={SP.max_edge})",
            )

        # Check exposure limits
        exposure = self._working.total_exposure()
        max_size = self._working.bankroll * SP.max_position_pct
        available = self._working.bankroll - exposure
        if available <= 0:
            return self._pass_decision(
                analysis, backtest, "No available bankroll (fully exposed)"
            )

        # Kelly criterion (mechanical)
        is_bootstrap = "Bootstrap mode" in backtest.details
        if is_bootstrap:
            kelly_fraction = SP.max_position_pct * SP.kelly_fraction
        else:
            kelly_fraction = self._kelly(
                backtest.simulated_win_rate, analysis.estimated_fair_value
            )
        size_usd = min(kelly_fraction * self._working.bankroll, max_size, available)

        # Reduce size if overallocated in this category (Track 7 portfolio targets)
        allocation = LINUX.get_category_allocation()
        if allocation:
            cat = getattr(analysis, "category", "other")
            target_pct = allocation.get(cat, 0.20)
            cat_exposure = sum(
                p.size_usd for p in self._working.open_positions()
                if getattr(p, "category", "other") == cat
            )
            cat_pct = cat_exposure / max(self._working.bankroll, 1)
            if cat_pct > target_pct * 1.5:
                size_usd *= 0.5
                logger.info(
                    f"Strategist: halving size — {cat} at {cat_pct:.0%} vs target {target_pct:.0%}"
                )

        if size_usd < 1.0:
            return self._pass_decision(
                analysis, backtest, f"Kelly size ${size_usd:.2f} too small"
            )

        # Determine direction
        action = "buy_yes" if analysis.edge > 0 else "buy_no"
        target_price = (
            analysis.estimated_fair_value
            if action == "buy_yes"
            else (1.0 - analysis.estimated_fair_value)
        )

        # Build reasoning mechanically — no LLM call needed.
        reasoning = (
            f"{action.upper()} @ {target_price:.3f} | "
            f"edge={analysis.edge:+.3f}, conf={analysis.confidence:.2f}, "
            f"bt_ev={backtest.simulated_ev:.4f}, bt_wr={backtest.simulated_win_rate:.2%}, "
            f"n={backtest.similar_markets_found}. "
            f"Analyst: {analysis.reasoning[:300]}"
        )

        decision = TradeDecision(
            market_id=analysis.market_id,
            platform=analysis.platform,
            action=action,
            target_price=target_price,
            size_usd=round(size_usd, 2),
            kelly_fraction=round(kelly_fraction, 4),
            reasoning=reasoning,
            backtest_ev=backtest.simulated_ev,
            backtest_sample=backtest.similar_markets_found,
            paper_only=True,  # always paper for now
        )

        logger.info(
            f"Strategist: {action} {analysis.question[:40]} "
            f"@ {target_price:.3f} (${size_usd:.2f}, kelly={kelly_fraction:.3f})"
        )
        return decision

    def _kelly(self, win_rate: float, entry_price: float) -> float:
        """Quarter-Kelly for binary bet. Capped at SP.max_position_pct."""
        if entry_price <= 0 or entry_price >= 1 or win_rate <= 0:
            return 0.0
        b = (1.0 - entry_price) / entry_price  # net odds
        q = 1.0 - win_rate
        kelly = (win_rate * b - q) / b
        quarter_kelly = max(kelly * SP.kelly_fraction, 0.0)
        return min(quarter_kelly, SP.max_position_pct)

    def _pass_decision(
        self,
        analysis: MarketAnalysis,
        backtest: BacktestResult,
        reason: str,
    ) -> TradeDecision:
        logger.info(f"Strategist: PASS on {analysis.market_id} — {reason}")
        return TradeDecision(
            market_id=analysis.market_id,
            platform=analysis.platform,
            action="pass",
            reasoning=reason,
            backtest_ev=backtest.simulated_ev,
            backtest_sample=backtest.similar_markets_found,
        )
