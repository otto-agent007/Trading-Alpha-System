from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from clients.base import MarketClient
from config import DATA_PATH
from core.memory.episodic import EpisodicMemory
from core.memory.working import WorkingMemory
from core.models import Market, TradeDecision

logger = logging.getLogger(__name__)

# All live order attempts (success + failure) are appended here as JSONL for audit.
_LIVE_AUDIT_LOG = DATA_PATH / "live_trades.jsonl"


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

        # Live execution safety gates — checked in order
        if not self._working.live_mode_enabled:
            logger.info("Executor: live_mode_enabled=False — falling back to paper mode")
            return self._paper_execute(decision, market)

        if self._working.check_circuit_breaker():
            logger.warning("Executor: circuit breaker active — falling back to paper mode")
            return self._paper_execute(decision, market)

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
        """Live trade: place order via the platform client.

        Only Polymarket is supported (Kalshi in a future release).
        Falls back to paper execution if the order cannot be placed.
        Every attempt — success or failure — is appended to live_trades.jsonl.
        """
        audit: dict = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "market_id": decision.market_id,
            "platform": decision.platform,
            "action": decision.action,
            "target_price": decision.target_price,
            "size_usd": decision.size_usd,
            "status": "attempted",
            "fill_price": None,
            "error": None,
        }

        if decision.platform != "polymarket":
            audit["status"] = "skipped"
            audit["error"] = f"Live execution not supported for platform: {decision.platform}"
            self._write_audit(audit)
            logger.warning(f"Executor [LIVE]: {audit['error']} — falling back to paper")
            return self._paper_execute(decision, market)

        client = self._clients.get("polymarket")
        if client is None:
            audit["status"] = "error"
            audit["error"] = "Polymarket client not found in clients dict"
            self._write_audit(audit)
            logger.error(f"Executor [LIVE]: {audit['error']}")
            return self._paper_execute(decision, market)

        # Determine CLOB side: buy_yes → BUY YES token; buy_no → BUY NO token
        # place_order handles token resolution internally
        clob_side = "BUY"
        token_type = "yes" if decision.action == "buy_yes" else "no"

        try:
            result = client.place_order(
                market_id=decision.market_id,
                side=clob_side,
                token_type=token_type,
                price=decision.target_price,
                size_usd=decision.size_usd,
            )

            if result and result.get("status") in ("matched", "live"):
                fill_price = float(result.get("price", decision.target_price))
                audit["status"] = "filled"
                audit["fill_price"] = fill_price
                self._write_audit(audit)

                # Record position using actual fill price (not target)
                from copy import copy
                filled_decision = copy(decision)
                filled_decision.target_price = fill_price
                self._working.record_position(filled_decision, market)
                self._log_episode(filled_decision, market, executed=True)

                logger.info(
                    f"Executor [LIVE]: {decision.action} {market.question[:50]} "
                    f"@ {fill_price:.3f} (${decision.size_usd:.2f})"
                )
                return True
            else:
                audit["status"] = "rejected"
                audit["error"] = str(result)
                self._write_audit(audit)
                logger.warning(
                    f"Executor [LIVE]: order rejected for {decision.market_id}: {result} "
                    "— falling back to paper"
                )
                return self._paper_execute(decision, market)

        except Exception as e:
            audit["status"] = "error"
            audit["error"] = str(e)
            self._write_audit(audit)
            logger.error(
                f"Executor [LIVE]: order failed for {decision.market_id}: {e} "
                "— falling back to paper",
                exc_info=True,
            )
            return self._paper_execute(decision, market)

    def _write_audit(self, entry: dict) -> None:
        """Append a live order audit record to live_trades.jsonl."""
        try:
            DATA_PATH.mkdir(parents=True, exist_ok=True)
            with _LIVE_AUDIT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Executor: failed to write audit log: {e}")

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
