from __future__ import annotations

import logging

import pandas as pd

from clients.base import MarketClient
from core.memory.episodic import EpisodicMemory
from core.models import BacktestResult, MarketAnalysis
from core.strategy_params import SP

logger = logging.getLogger(__name__)

# Bootstrap continues until we have this many RESOLVED trades in memory.
# Prevents deadlock: unresolved pending trades no longer block bootstrap mode.
BOOTSTRAP_RESOLVED_THRESHOLD = 4


class Backtester:
    """Validates an analyst's edge estimate against historical data.

    Finds similar resolved markets via episodic memory, fetches their
    price histories, and simulates what would have happened if we had
    entered at the analyst's estimated fair value.

    This is the gate: no trade proceeds without a passing backtest.
    """

    def __init__(
        self,
        clients: dict[str, MarketClient],
        episodic: EpisodicMemory,
    ) -> None:
        self._clients = clients
        self._episodic = episodic

    def validate(self, analysis: MarketAnalysis) -> BacktestResult:
        """Run historical validation on a MarketAnalysis.

        Returns BacktestResult with passed=True/False.
        """
        # Find similar resolved markets from episodic memory
        similar = self._episodic.recall(
            f"resolved market similar to: {analysis.question} (category: {analysis.platform})",
            n=30,
        )

        # Filter to resolved episodes only
        resolved = [
            ep for ep in similar
            if ep.get("outcome") and ep.get("outcome") != "pending"
        ]

        if len(resolved) < SP.bt_min_sample:
            # Bootstrap mode: auto-pass until we have BOOTSTRAP_RESOLVED_THRESHOLD
            # *resolved* outcomes in memory. This fixes the deadlock where unresolved
            # pending trades fill the episode count but never provide useful history.
            resolved_count = len(resolved)
            if resolved_count < BOOTSTRAP_RESOLVED_THRESHOLD:
                logger.info(
                    f"Backtester: bootstrap mode — only {resolved_count} resolved episodes "
                    f"(need {BOOTSTRAP_RESOLVED_THRESHOLD} to exit bootstrap), "
                    f"auto-passing to build history (paper trades only)"
                )
                return BacktestResult(
                    market_id=analysis.market_id,
                    similar_markets_found=resolved_count,
                    simulated_win_rate=0.0,
                    simulated_ev=0.0,
                    simulated_max_drawdown=0.0,
                    avg_entry_price=analysis.estimated_fair_value,
                    passed=True,
                    details=f"Bootstrap mode: {resolved_count} resolved episodes, building history",
                )

            return BacktestResult(
                market_id=analysis.market_id,
                similar_markets_found=resolved_count,
                simulated_win_rate=0.0,
                simulated_ev=0.0,
                simulated_max_drawdown=0.0,
                avg_entry_price=analysis.estimated_fair_value,
                passed=False,
                details=f"Insufficient similar resolved markets: {resolved_count} (need {SP.bt_min_sample})",
            )

        # Simulate trades on each similar market
        results = self._simulate_trades(resolved, analysis)

        if not results:
            return BacktestResult(
                market_id=analysis.market_id,
                similar_markets_found=len(resolved),
                simulated_win_rate=0.0,
                simulated_ev=0.0,
                simulated_max_drawdown=0.0,
                avg_entry_price=analysis.estimated_fair_value,
                passed=False,
                details="No valid simulations could be run",
            )

        # Aggregate results
        wins = sum(1 for r in results if r["pnl"] > 0)
        win_rate = wins / len(results)
        avg_ev = sum(r["pnl"] for r in results) / len(results)
        avg_entry = sum(r["entry_price"] for r in results) / len(results)

        # Calculate max drawdown from cumulative PnL
        cumulative = 0.0
        peak = 0.0
        max_dd = 0.0
        for r in results:
            cumulative += r["pnl"]
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / max(peak, 1.0) if peak > 0 else 0.0
            max_dd = max(max_dd, dd)

        # Gate checks
        passed = (
            len(results) >= SP.bt_min_sample
            and avg_ev > SP.bt_min_ev
            and win_rate > SP.bt_min_win_rate
            and max_dd < SP.bt_max_drawdown
        )

        reasons = []
        if len(results) < SP.bt_min_sample:
            reasons.append(f"sample={len(results)}<{SP.bt_min_sample}")
        if avg_ev <= SP.bt_min_ev:
            reasons.append(f"EV={avg_ev:.4f}<={SP.bt_min_ev}")
        if win_rate <= SP.bt_min_win_rate:
            reasons.append(f"win_rate={win_rate:.2%}<={SP.bt_min_win_rate:.0%}")
        if max_dd >= SP.bt_max_drawdown:
            reasons.append(f"max_dd={max_dd:.2%}>={SP.bt_max_drawdown:.0%}")

        details = (
            f"n={len(results)}, win_rate={win_rate:.2%}, EV={avg_ev:.4f}, max_dd={max_dd:.2%}"
            if passed
            else f"FAILED: {'; '.join(reasons)}"
        )

        result = BacktestResult(
            market_id=analysis.market_id,
            similar_markets_found=len(results),
            simulated_win_rate=win_rate,
            simulated_ev=avg_ev,
            simulated_max_drawdown=max_dd,
            avg_entry_price=avg_entry,
            passed=passed,
            details=details,
        )

        logger.info(f"Backtester: {analysis.market_id} → {details}")
        return result

    def _simulate_trades(
        self,
        resolved: list[dict],
        analysis: MarketAnalysis,
    ) -> list[dict]:
        """Simulate entering at the analysis's fair value on similar markets."""
        simulated: list[dict] = []
        edge = analysis.edge  # fair_value - current_price

        for ep in resolved:
            outcome = ep.get("outcome", "")
            entry_price = analysis.estimated_fair_value

            # Determine if we would have gone YES or NO
            # Positive edge → we think YES is underpriced → buy YES
            # Negative edge → we think YES is overpriced → buy NO
            if edge > 0:
                # Buy YES at entry_price
                won = outcome.lower() in ("yes", "win", "true", "1")
                if won:
                    pnl = (1.0 - entry_price)  # profit per dollar of risk
                else:
                    pnl = -entry_price  # loss per dollar of risk
            else:
                # Buy NO at (1 - entry_price)
                no_price = 1.0 - entry_price
                won = outcome.lower() in ("no", "lose", "false", "0")
                if won:
                    pnl = (1.0 - no_price)
                else:
                    pnl = -no_price

            simulated.append({
                "market_id": ep.get("market_id", ""),
                "entry_price": entry_price,
                "outcome": outcome,
                "won": won,
                "pnl": pnl,
            })

        return simulated
