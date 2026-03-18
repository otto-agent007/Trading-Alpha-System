from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime

from config import OBSIDIAN_VAULT
from core.memory.semantic import SemanticMemory
from core.memory.working import WorkingMemory
from core.models import BacktestResult, MarketAnalysis, TradeDecision

logger = logging.getLogger(__name__)

VAULT_DIR = OBSIDIAN_VAULT / "Alpha Research"
DASHBOARD_DIR = VAULT_DIR / "Dashboard"


class ObsidianWriter:
    """Writes analysis, decisions, and dashboards to the Obsidian vault.

    All files include YAML frontmatter for Dataview queries.
    Gracefully skips writes if the vault directory can't be created.
    """

    def __init__(
        self,
        semantic: SemanticMemory,
        working: WorkingMemory,
    ) -> None:
        self._semantic = semantic
        self._working = working
        self._vault_ok = self._ensure_vault()

    def _ensure_vault(self) -> bool:
        """Try to create the vault directory. Returns True if writable.

        Uses multiple strategies for Windows compatibility (junctions, OneDrive paths, etc).
        Logs the resolved path so misconfiguration is easy to spot.
        """
        logger.info(f"ObsidianWriter: vault root = {repr(str(OBSIDIAN_VAULT))}")
        logger.info(f"ObsidianWriter: vault_dir  = {repr(str(VAULT_DIR))}")

        # Verify the vault root actually exists before trying to create children
        if not OBSIDIAN_VAULT.exists():
            logger.warning(
                f"ObsidianWriter: vault root does not exist: {OBSIDIAN_VAULT}. "
                "Set OBSIDIAN_VAULT in .env to a valid path. Writes will be skipped."
            )
            return False

        for d in (VAULT_DIR, DASHBOARD_DIR):
            if d.exists():
                continue  # already there
            # Strategy 1: pathlib with parents=True
            try:
                d.mkdir(parents=True, exist_ok=True)
                continue
            except OSError:
                pass
            # Strategy 2: os.makedirs with string path
            try:
                os.makedirs(str(d), exist_ok=True)
                continue
            except OSError:
                pass
            # Strategy 3: os.mkdir on just the leaf (parent already exists)
            try:
                os.mkdir(str(d))
                continue
            except OSError:
                pass
            # Strategy 4: forward-slash path (bypass Windows junction issues)
            try:
                fwd = str(d).replace("\\", "/")
                os.makedirs(fwd, exist_ok=True)
                continue
            except OSError as e:
                logger.warning(
                    f"ObsidianWriter: all mkdir strategies failed for {d!r} ({e}). "
                    "Writes will be skipped."
                )
                return False

        logger.info(f"ObsidianWriter: vault ready at {VAULT_DIR}")
        return True

    def write_analysis(self, analysis: MarketAnalysis) -> None:
        """Write a per-market analysis file."""
        if not self._vault_ok:
            return
        safe_id = analysis.market_id[:40].replace("/", "_")
        path = VAULT_DIR / f"Analysis_{safe_id}.md"

        lines = [
            "---",
            f"type: analysis",
            f"market_id: \"{analysis.market_id}\"",
            f"platform: {analysis.platform}",
            f"category: \"\"",
            f"status: open",
            f"ev: {analysis.edge:.4f}",
            f"date: {datetime.now().strftime('%Y-%m-%d')}",
            f"tags: [alpha, analysis]",
            "---",
            "",
            f"# {analysis.question}",
            "",
            f"**Platform:** {analysis.platform}  ",
            f"**Current YES price:** {analysis.current_price:.3f}  ",
            f"**Estimated fair value:** {analysis.estimated_fair_value:.3f}  ",
            f"**Edge:** {analysis.edge:+.3f}  ",
            f"**Confidence:** {analysis.confidence:.2f}  ",
            "",
            "## Reasoning",
            "",
            analysis.reasoning,
            "",
            f"**Orderbook:** {analysis.orderbook_summary}",
            "",
            "## Similar Past Markets",
            "",
        ]
        for mid in analysis.similar_past_markets:
            lines.append(f"- {mid}")
        lines += [
            "",
            "## Applicable Patterns",
            "",
        ]
        for pat in analysis.applicable_patterns:
            lines.append(f"- {pat}")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"ObsidianWriter: wrote {path.name}")

    def write_decision(self, decision: TradeDecision, analysis: MarketAnalysis) -> None:
        """Write a trade decision file."""
        if not self._vault_ok:
            return
        path = VAULT_DIR / f"Decision_{decision.id}.md"

        status = "pass" if decision.action == "pass" else "open"
        lines = [
            "---",
            f"type: decision",
            f"market_id: \"{decision.market_id}\"",
            f"platform: {decision.platform}",
            f"accepted: {decision.action != 'pass'}",
            f"ev: {decision.backtest_ev:.4f}",
            f"date: {datetime.now().strftime('%Y-%m-%d')}",
            f"tags: [alpha, decision, {'accepted' if decision.action != 'pass' else 'rejected'}]",
            "---",
            "",
            f"# Decision: {analysis.question}",
            "",
            f"**Action:** `{decision.action}`  ",
            f"**Target price:** {decision.target_price:.3f}  ",
            f"**Size:** ${decision.size_usd:.2f}  ",
            f"**Kelly fraction:** {decision.kelly_fraction:.4f}  ",
            f"**Backtest EV:** {decision.backtest_ev:.4f} (n={decision.backtest_sample})  ",
            f"**Paper only:** {decision.paper_only}  ",
            "",
            "## Reasoning",
            "",
            decision.reasoning,
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"ObsidianWriter: wrote {path.name}")

    def write_daily_review(self, stats: dict) -> None:
        """Write daily review dashboard with embedded Dataview queries."""
        if not self._vault_ok:
            return
        date_str = datetime.now().strftime("%Y-%m-%d")
        path = DASHBOARD_DIR / f"Daily_Review_{date_str}.md"

        positions = self._working.open_positions()
        lines = [
            "---",
            f"type: review",
            f"date: {date_str}",
            f"tags: [review, daily]",
            "---",
            "",
            f"# Daily Review — {date_str}",
            "",
            f"**Bankroll:** ${stats.get('bankroll', 0):.2f}  ",
            f"**Open positions:** {stats.get('open_positions', 0)}  ",
            f"**Total exposure:** ${stats.get('total_exposure', 0):.2f}  ",
            f"**Resolved today:** {stats.get('resolved_positions', 0)}  ",
            f"**Learned patterns:** {stats.get('learned_patterns', 0)}  ",
            f"**Total episodes:** {stats.get('total_episodes', 0)}  ",
            "",
            "## Open Positions",
            "",
            "| Market | Direction | Entry | Current | PnL |",
            "|--------|-----------|-------|---------|-----|",
        ]
        for pos in positions:
            pnl = (pos.current_price - pos.entry_price) * pos.size_usd / pos.entry_price
            lines.append(
                f"| {pos.question[:40]} | {pos.direction} | "
                f"{pos.entry_price:.3f} | {pos.current_price:.3f} | ${pnl:+.2f} |"
            )
        lines += [
            "",
            "## Recent Decisions",
            "",
            "```dataview",
            "TABLE market_id, platform, ev, accepted, date",
            f'FROM "Alpha Research"',
            'WHERE type = "decision"',
            "SORT date DESC",
            "LIMIT 20",
            "```",
            "",
            "## Top Patterns",
            "",
            "```dataview",
            "TABLE category, pattern, confidence, evidence_count",
            f'FROM "Alpha Research"',
            'WHERE type = "pattern"',
            "SORT confidence DESC",
            "LIMIT 10",
            "```",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"ObsidianWriter: wrote {path.name}")

    def write_patterns(self) -> None:
        """Update the master Patterns.md with all semantic memory patterns."""
        if not self._vault_ok:
            return
        path = VAULT_DIR / "Patterns.md"

        all_patterns = self._semantic.get_all()
        sorted_patterns = sorted(
            all_patterns,
            key=lambda p: float(p.get("confidence", 0)),
            reverse=True,
        )

        lines = [
            "---",
            "type: pattern",
            f"date: {datetime.now().strftime('%Y-%m-%d')}",
            "tags: [patterns, auto-updated]",
            "---",
            "",
            "# Learned Patterns",
            "",
            f"*Auto-updated. Total patterns: {len(sorted_patterns)}*",
            "",
            "| Category | Pattern | Confidence | Evidence | Updated |",
            "|----------|---------|------------|----------|---------|",
        ]
        for p in sorted_patterns:
            cat = p.get("category", "?")
            pat = p.get("pattern", "?")[:80]
            conf = f"{float(p.get('confidence', 0)):.2f}"
            ev = p.get("evidence_count", 0)
            updated = p.get("updated", "?")[:10]
            lines.append(f"| {cat} | {pat} | {conf} | {ev} | {updated} |")

        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"ObsidianWriter: wrote Patterns.md ({len(sorted_patterns)} patterns)")

    def write_excalidraw_portfolio(self) -> None:
        """Generate an Excalidraw diagram of current positions."""
        if not self._vault_ok:
            return
        positions = self._working.open_positions()
        if not positions:
            return

        elements = []
        x = 100
        y = 100
        for i, pos in enumerate(positions):
            pnl = (pos.current_price - pos.entry_price) * pos.size_usd / pos.entry_price
            color = "#2ecc71" if pnl >= 0 else "#e74c3c"
            width = max(80, int(pos.size_usd / 2))
            height = 60

            elements.append({
                "id": str(uuid.uuid4()),
                "type": "rectangle",
                "x": x + (i % 4) * 220,
                "y": y + (i // 4) * 120,
                "width": width,
                "height": height,
                "backgroundColor": color,
                "fillStyle": "solid",
                "strokeWidth": 1,
                "strokeColor": "#333",
                "roughness": 0,
            })
            elements.append({
                "id": str(uuid.uuid4()),
                "type": "text",
                "x": x + (i % 4) * 220 + 5,
                "y": y + (i // 4) * 120 + 5,
                "width": width - 10,
                "height": 50,
                "text": f"{pos.direction.upper()}\n{pos.question[:20]}\n${pnl:+.2f}",
                "fontSize": 12,
                "fontFamily": 1,
            })

        excalidraw_data = {
            "type": "excalidraw",
            "version": 2,
            "source": "trading-alpha-system",
            "elements": elements,
            "appState": {"viewBackgroundColor": "#ffffff"},
        }

        path = DASHBOARD_DIR / "Portfolio_Map.excalidraw.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "%%\n"
            f"# Excalidraw Data\n"
            f"```json\n"
            f"{json.dumps(excalidraw_data, indent=2)}\n"
            f"```\n"
            "%%\n"
        )
        path.write_text(content, encoding="utf-8")
        logger.info("ObsidianWriter: wrote Portfolio_Map.excalidraw.md")

    # ------------------------------------------------------------------
    # Scan + Backtest audit trail
    # ------------------------------------------------------------------

    def write_scan_summary(self, scan_stats: dict) -> None:
        """Write a summary of a scan cycle to the Dashboard folder."""
        if not self._vault_ok:
            return
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H%M")

        added = scan_stats.get("added", 0)
        total_scanned = scan_stats.get("total_scanned", 0)
        keyword_hits = scan_stats.get("keyword_hits", 0)
        keyword_filtered = scan_stats.get("keyword_filtered", 0)
        heuristic_hits = scan_stats.get("heuristic_hits", 0)
        watchlist_size = scan_stats.get("watchlist_size", 0)

        lines = [
            "---",
            "type: scan",
            f"date: {date_str}",
            f"added: {added}",
            f"scanned: {total_scanned}",
            "tags: [scan, auto-generated]",
            "---",
            "",
            f"# Scan Summary — {date_str} {now.strftime('%H:%M')} UTC",
            "",
            f"**Markets scanned:** {total_scanned}  ",
            f"**Added to watchlist:** {added}  ",
            f"**Keyword hits (live):** {keyword_hits}  ",
            f"**Keyword hits (dead/filtered):** {keyword_filtered}  ",
            f"**Heuristic/pattern hits:** {heuristic_hits}  ",
            f"**Current watchlist size:** {watchlist_size}  ",
            "",
            "## Recent Scans",
            "",
            "```dataview",
            "TABLE added, scanned, date",
            'FROM "Alpha Research/Dashboard"',
            'WHERE type = "scan"',
            "SORT date DESC",
            "LIMIT 10",
            "```",
        ]

        path = DASHBOARD_DIR / f"Scan_{date_str}_{time_str}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"ObsidianWriter: wrote {path.name}")

    def write_backtest_result(
        self, analysis: MarketAnalysis, bt_result: BacktestResult
    ) -> None:
        """Write a backtest result file (pass or fail) for audit trail."""
        if not self._vault_ok:
            return
        safe_id = bt_result.market_id[:40].replace("/", "_")
        status_label = "PASSED" if bt_result.passed else "FAILED"
        date_str = datetime.now().strftime("%Y-%m-%d")

        lines = [
            "---",
            "type: backtest",
            f'market_id: "{bt_result.market_id}"',
            f"platform: {analysis.platform}",
            f"passed: {str(bt_result.passed).lower()}",
            f"win_rate: {bt_result.simulated_win_rate:.4f}",
            f"ev: {bt_result.simulated_ev:.4f}",
            f"max_drawdown: {bt_result.simulated_max_drawdown:.4f}",
            f"sample_size: {bt_result.similar_markets_found}",
            f"date: {date_str}",
            f"tags: [backtest, {'passed' if bt_result.passed else 'failed'}]",
            "---",
            "",
            f"# Backtest: {analysis.question}",
            "",
            f"**Result:** `{status_label}`  ",
            f"**Platform:** {analysis.platform}  ",
            "",
            "## Analysis Context",
            "",
            f"**Current YES price:** {analysis.current_price:.3f}  ",
            f"**Estimated fair value:** {analysis.estimated_fair_value:.3f}  ",
            f"**Edge:** {analysis.edge:+.3f}  ",
            f"**Confidence:** {analysis.confidence:.2f}  ",
            "",
            "## Backtest Stats",
            "",
            f"**Similar markets found:** {bt_result.similar_markets_found}  ",
            f"**Simulated win rate:** {bt_result.simulated_win_rate:.2%}  ",
            f"**Simulated EV:** {bt_result.simulated_ev:.4f}  ",
            f"**Max drawdown:** {bt_result.simulated_max_drawdown:.2%}  ",
            f"**Avg entry price:** {bt_result.avg_entry_price:.3f}  ",
            "",
            "## Details",
            "",
            bt_result.details,
        ]

        path = VAULT_DIR / f"Backtest_{safe_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"ObsidianWriter: wrote {path.name} ({status_label})")
