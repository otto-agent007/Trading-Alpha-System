from __future__ import annotations

import logging

import pandas as pd

from clients.base import MarketClient
from core.memory.episodic import EpisodicMemory
from core.models import BacktestResult, MarketAnalysis
from core.strategy_params import SP

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bootstrap graduation thresholds
#
# Instead of a single threshold (old: 4 resolved → full pass), we use a
# graduated ramp.  Each tier unlocks slightly larger positions and requires
# slightly more evidence before a full backtest is expected.
#
#   Tier 0: 0 resolved episodes   → BLOCK (don't trade blind)
#   Tier 1: 1-3 resolved episodes → allow, but micro-size (0.5% bankroll)
#   Tier 2: 4-7 resolved episodes → allow, reduced size (1% bankroll)
#   Tier 3: 8+ resolved episodes  → full backtest required (normal flow)
#
# The strategist reads the "bootstrap_tier" from the details string and
# scales position size accordingly.
# ---------------------------------------------------------------------------

BOOTSTRAP_TIER_0_MAX = 0   # 0 resolved → blocked
BOOTSTRAP_TIER_1_MAX = 3   # 1-3 resolved → micro positions
BOOTSTRAP_TIER_2_MAX = 7   # 4-7 resolved → reduced positions


class Backtester:
    """Validates an analyst's edge estimate against historical data.

    Finds similar resolved markets via episodic memory, fetches their
    price histories, and simulates what would have happened if we had
    entered at the analyst's estimated fair value.

    This is the gate: no trade proceeds without a passing backtest.

    Bootstrap mode now uses graduated tiers instead of a blanket auto-pass,
    and reports the tier so the strategist can scale position sizes.
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
        resolved_count = len(resolved)

        if resolved_count < SP.bt_min_sample:
            return self._bootstrap_decision(analysis, resolved_count, resolved)

        # Full backtest: simulate trades on each similar market
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

    def _bootstrap_decision(
        self,
        analysis: MarketAnalysis,
        resolved_count: int,
        resolved: list[dict],
    ) -> BacktestResult:
        """Graduated bootstrap: tier determines position sizing.

        Tier 0 (0 resolved):   BLOCK — no trading without any history
        Tier 1 (1-3 resolved): micro positions, high confidence required
        Tier 2 (4-7 resolved): reduced positions, run partial validation
        """
        # Tier 0: hard block
        if resolved_count <= BOOTSTRAP_TIER_0_MAX:
            logger.info(
                f"Backtester: bootstrap BLOCKED — 0 resolved episodes in memory. "
                f"Run 'python scripts/seed_memory.py' to populate history."
            )
            return BacktestResult(
                market_id=analysis.market_id,
                similar_markets_found=0,
                simulated_win_rate=0.0,
                simulated_ev=0.0,
                simulated_max_drawdown=0.0,
                avg_entry_price=analysis.estimated_fair_value,
                passed=False,
                details=(
                    "Bootstrap BLOCKED: 0 resolved episodes. "
                    "Seed memory with scripts/seed_memory.py first."
                ),
            )

        # Tier 1: micro positions
        if resolved_count <= BOOTSTRAP_TIER_1_MAX:
            # Run whatever validation we can with the few episodes we have
            partial_results = self._simulate_trades(resolved, analysis)
            partial_wr = 0.0
            partial_ev = 0.0
            if partial_results:
                wins = sum(1 for r in partial_results if r["pnl"] > 0)
                partial_wr = wins / len(partial_results)
                partial_ev = sum(r["pnl"] for r in partial_results) / len(partial_results)

            logger.info(
                f"Backtester: bootstrap tier 1 — {resolved_count} resolved episodes, "
                f"micro-size allowed (partial wr={partial_wr:.0%}, ev={partial_ev:.3f})"
            )
            return BacktestResult(
                market_id=analysis.market_id,
                similar_markets_found=resolved_count,
                simulated_win_rate=partial_wr,
                simulated_ev=partial_ev,
                simulated_max_drawdown=0.0,
                avg_entry_price=analysis.estimated_fair_value,
                passed=True,
                details=(
                    f"Bootstrap tier 1: {resolved_count} resolved episodes, "
                    f"micro-size (0.5% max). partial_wr={partial_wr:.0%}"
                ),
            )

        # Tier 2: reduced positions
        partial_results = self._simulate_trades(resolved, analysis)
        partial_wr = 0.0
        partial_ev = 0.0
        if partial_results:
            wins = sum(1 for r in partial_results if r["pnl"] > 0)
            partial_wr = wins / len(partial_results)
            partial_ev = sum(r["pnl"] for r in partial_results) / len(partial_results)

        # Tier 2 has a soft gate: if partial evidence is strongly negative, block
        if partial_results and partial_ev < -0.10:
            logger.info(
                f"Backtester: bootstrap tier 2 BLOCKED — partial evidence strongly negative "
                f"(ev={partial_ev:.3f}, n={resolved_count})"
            )
            return BacktestResult(
                market_id=analysis.market_id,
                similar_markets_found=resolved_count,
                simulated_win_rate=partial_wr,
                simulated_ev=partial_ev,
                simulated_max_drawdown=0.0,
                avg_entry_price=analysis.estimated_fair_value,
                passed=False,
                details=(
                    f"Bootstrap tier 2 BLOCKED: partial evidence negative "
                    f"(ev={partial_ev:.3f}, n={resolved_count})"
                ),
            )

        logger.info(
            f"Backtester: bootstrap tier 2 — {resolved_count} resolved episodes, "
            f"reduced-size allowed (partial wr={partial_wr:.0%}, ev={partial_ev:.3f})"
        )
        return BacktestResult(
            market_id=analysis.market_id,
            similar_markets_found=resolved_count,
            simulated_win_rate=partial_wr,
            simulated_ev=partial_ev,
            simulated_max_drawdown=0.0,
            avg_entry_price=analysis.estimated_fair_value,
            passed=True,
            details=(
                f"Bootstrap tier 2: {resolved_count} resolved episodes, "
                f"reduced-size (1% max). partial_wr={partial_wr:.0%}"
            ),
        )

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
