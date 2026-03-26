from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from config import DATA_PATH
from core.models import Market, Position, TradeDecision, WatchlistItem

logger = logging.getLogger(__name__)

WORKING_MEMORY_FILE = DATA_PATH / "working_memory.json"


class WorkingMemory(BaseModel):
    """Current system state — persisted to JSON between scheduler runs.

    IMPORTANT: Methods that mutate state set ``_dirty`` but do NOT auto-save.
    The caller (main.py loop, or the method itself for critical ops) calls
    ``save()`` once at the end.  This avoids 20+ redundant writes during a
    single scan cycle that adds many markets.

    Critical mutations (position open/close, bankroll change) still auto-save
    because losing those is unacceptable.
    """

    positions: list[Position] = Field(default_factory=list)
    watchlist: list[WatchlistItem] = Field(default_factory=list)
    pending_analyses: list[str] = Field(default_factory=list)  # market_ids
    last_scan: datetime | None = None
    last_analysis: datetime | None = None
    bankroll: float = 1000.0

    # Live trading safety controls.
    # live_mode_enabled must be explicitly set to True — never auto-enables.
    live_mode_enabled: bool = False
    # circuit_breaker_triggered auto-sets on >10% daily loss; resets at 03:00 UTC review.
    circuit_breaker_triggered: bool = False
    # Bankroll snapshot at the start of each UTC day (set by reset_daily_tracking).
    daily_loss_start: float = 0.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self) -> None:
        """Atomic write: tmp -> fsync -> rename.  Prevents corruption on crash."""
        DATA_PATH.mkdir(parents=True, exist_ok=True)
        data = self.model_dump_json(indent=2).encode("utf-8")
        fd = None
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(DATA_PATH), suffix=".tmp", prefix="wm_"
            )
            os.write(fd, data)
            os.fsync(fd)
            os.close(fd)
            fd = None  # closed successfully
            os.replace(tmp_path, str(WORKING_MEMORY_FILE))  # atomic on POSIX
            tmp_path = None  # replaced successfully
            logger.debug("WorkingMemory saved to disk")
        except Exception:
            # Clean up on failure
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            raise

    @classmethod
    def load(cls) -> WorkingMemory:
        if WORKING_MEMORY_FILE.exists():
            try:
                data = json.loads(WORKING_MEMORY_FILE.read_text(encoding="utf-8"))
                wm = cls.model_validate(data)
                logger.info(
                    f"WorkingMemory loaded: {len(wm.positions)} positions, "
                    f"{len(wm.watchlist)} watchlist items, bankroll=${wm.bankroll:.2f}"
                )
                return wm
            except Exception as e:
                logger.warning(f"WorkingMemory load failed ({e}), starting fresh")
                # Keep a backup of the corrupted file for forensics
                backup = WORKING_MEMORY_FILE.with_suffix(".json.corrupt")
                try:
                    WORKING_MEMORY_FILE.rename(backup)
                    logger.info(f"Corrupted file backed up to {backup}")
                except OSError:
                    pass
        from config import INITIAL_BANKROLL
        return cls(bankroll=INITIAL_BANKROLL)

    # ------------------------------------------------------------------
    # Watchlist  (caller should call save() after a batch of adds)
    # ------------------------------------------------------------------

    def add_to_watchlist(self, market: Market, reason: str, pattern_score: float = 0.0) -> None:
        if any(w.market_id == market.id for w in self.watchlist):
            return  # already watching
        self.watchlist.append(
            WatchlistItem(
                market_id=market.id,
                platform=market.platform,
                question=market.question,
                category=market.category,
                added_at=datetime.now(timezone.utc),
                reason=reason,
                pattern_match_score=pattern_score,
            )
        )
        logger.info(f"Watchlist +1: {market.question[:60]} ({reason})")
        # NOT auto-saving — scanner calls save() once after the full scan

    def remove_from_watchlist(self, market_id: str) -> None:
        self.watchlist = [w for w in self.watchlist if w.market_id != market_id]
        self.pending_analyses = [m for m in self.pending_analyses if m != market_id]
        # NOT auto-saving — caller saves when appropriate

    def get_top_watchlist(self, n: int = 5) -> list[WatchlistItem]:
        """Return top-N watchlist items sorted by pattern match score."""
        return sorted(self.watchlist, key=lambda w: w.pattern_match_score, reverse=True)[:n]

    # ------------------------------------------------------------------
    # Positions  (these auto-save — losing a position record is unacceptable)
    # ------------------------------------------------------------------

    def record_position(self, decision: TradeDecision, market: Market) -> None:
        direction = "yes" if decision.action == "buy_yes" else "no"
        self.positions.append(
            Position(
                market_id=decision.market_id,
                platform=market.platform,
                question=market.question,
                category=market.category,
                direction=direction,
                entry_price=decision.target_price,
                size_usd=decision.size_usd,
                entry_time=datetime.now(timezone.utc),
                current_price=decision.target_price,
            )
        )
        self.bankroll -= decision.size_usd
        logger.info(
            f"Position opened: {direction.upper()} {market.question[:40]} "
            f"@ {decision.target_price:.3f} (${decision.size_usd:.2f})"
        )
        self.save()  # critical — auto-save

    def resolve_position(self, market_id: str, resolved_outcome: str) -> float:
        """Mark a position as closed and calculate PnL. Returns PnL."""
        pnl = 0.0
        for pos in self.positions:
            if pos.market_id == market_id and pos.status == "open":
                won = (
                    (pos.direction == "yes" and resolved_outcome == "Yes")
                    or (pos.direction == "no" and resolved_outcome == "No")
                )
                if won:
                    pnl = pos.size_usd * (1.0 - pos.entry_price) / pos.entry_price
                else:
                    pnl = -pos.size_usd
                pos.pnl = pnl
                pos.status = "closed"
                pos.resolved_outcome = resolved_outcome
                self.bankroll += pos.size_usd + pnl
                logger.info(
                    f"Position closed: {market_id} -> {resolved_outcome} "
                    f"(PnL: ${pnl:+.2f}, bankroll: ${self.bankroll:.2f})"
                )
        self.save()  # critical — auto-save
        return pnl

    def open_positions(self) -> list[Position]:
        return [p for p in self.positions if p.status == "open"]

    def total_exposure(self) -> float:
        return sum(p.size_usd for p in self.open_positions())

    # ------------------------------------------------------------------
    # Live trading safety controls
    # ------------------------------------------------------------------

    def check_circuit_breaker(self) -> bool:
        """Return True if live trading should be blocked.

        Checks both the explicit flag and the daily loss limit (10%).
        Auto-triggers and saves if the loss threshold is crossed.
        """
        if self.circuit_breaker_triggered:
            return True
        if self.daily_loss_start > 0:
            loss_pct = (self.bankroll - self.daily_loss_start) / self.daily_loss_start
            if loss_pct < -0.10:
                self.circuit_breaker_triggered = True
                logger.warning(
                    f"Circuit breaker TRIGGERED: bankroll dropped {loss_pct:.1%} today "
                    f"(${self.bankroll:.2f} vs start ${self.daily_loss_start:.2f})"
                )
                self.save()
                return True
        return False

    def reset_daily_tracking(self) -> None:
        """Reset daily loss baseline and circuit breaker. Call at start of each review cycle."""
        self.daily_loss_start = self.bankroll
        self.circuit_breaker_triggered = False
        self.save()
        logger.info(f"Daily tracking reset — loss baseline: ${self.daily_loss_start:.2f}")
