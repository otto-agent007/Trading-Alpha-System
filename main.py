from dotenv import load_dotenv
load_dotenv()  # must run before config.py is imported (it reads os.getenv at import time)

import logging
import time
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from agents.analyst import Analyst
from agents.backtester import Backtester
from agents.executor import Executor
from agents.obsidian import ObsidianWriter
from agents.reviewer import Reviewer
from agents.scanner import Scanner
from agents.strategist import Strategist
from clients.kalshi import KalshiClient
from clients.polymarket import PolymarketClient
from config import (
    ANALYSIS_BATCH_SIZE,
    ANALYSIS_INTERVAL_HOURS,
    REVIEW_HOUR_UTC,
    SCAN_INTERVAL_HOURS,
)
from core.linux_handoff import LINUX
from core.memory.episodic import EpisodicMemory
from core.memory.semantic import SemanticMemory
from core.memory.working import WorkingMemory
from core.models import WatchlistItem
from core.router import ModelRouter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared instances
# ---------------------------------------------------------------------------

router = ModelRouter()
episodic = EpisodicMemory()
semantic = SemanticMemory()
working = WorkingMemory.load()

poly_client = PolymarketClient()
kalshi_client = KalshiClient()

clients_list = [poly_client, kalshi_client]
clients_dict = {"polymarket": poly_client, "kalshi": kalshi_client}

scanner = Scanner(clients_list, router, semantic, working)
analyst = Analyst(clients_dict, router, episodic, semantic, working)
backtester = Backtester(clients_dict, episodic)
strategist = Strategist(router, working)
executor = Executor(clients_dict, episodic, working)
reviewer = Reviewer(clients_dict, router, episodic, semantic, working)
obsidian = ObsidianWriter(semantic, working)

# ---------------------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------------------


def scan_task() -> None:
    """Scanner loop — discover and watchlist interesting markets."""
    logger.info(f"[{datetime.now(timezone.utc).isoformat()}] Scan cycle started")
    try:
        scan_stats = scanner.run()
        obsidian.write_scan_summary(scan_stats)
        logger.info(
            f"Scan complete — {scan_stats['added']} added "
            f"(kw={scan_stats['keyword_hits']}, heur={scan_stats['heuristic_hits']}, "
            f"dead_filtered={scan_stats['keyword_filtered']})"
        )
    except Exception as e:
        logger.error(f"Scan cycle failed: {e}", exc_info=True)

    # Fast alerts — time-sensitive triggers from the news sentinel (linux box Track 6).
    # These bypass the normal scan cycle and queue markets for immediate analysis.
    try:
        alerts = LINUX.get_fast_alerts()
        if alerts:
            existing_ids = {w.market_id for w in working.watchlist}
            open_ids = {p.market_id for p in working.open_positions()}
            fast_added = 0
            for alert in alerts:
                mid = alert.get("market_id")
                if not mid or mid in existing_ids or mid in open_ids:
                    continue
                working.watchlist.append(WatchlistItem(
                    market_id=mid,
                    platform=alert.get("platform", "polymarket"),
                    question=alert.get("question", mid),
                    category=alert.get("category", "other"),
                    added_at=datetime.now(timezone.utc),
                    reason="fast_alert",
                    pattern_match_score=0.95,  # near top of queue
                ))
                existing_ids.add(mid)
                fast_added += 1
                logger.info(f"Fast alert: queued {mid} for immediate analysis")
            if fast_added:
                working.save()
                logger.info(
                    f"Fast alerts: {fast_added} new markets queued, triggering immediate analysis"
                )
                analysis_task()
    except Exception as e:
        logger.error(f"Fast alert processing failed: {e}", exc_info=True)


def analysis_task() -> None:
    """Analysis loop — analyze, backtest, decide, execute (batch)."""
    logger.info(f"[{datetime.now(timezone.utc).isoformat()}] Analysis cycle started")

    top_items = working.get_top_watchlist(n=ANALYSIS_BATCH_SIZE)
    if not top_items:
        logger.info("Analysis cycle: no markets to analyze")
        return

    traded = 0
    for item in top_items:
        try:
            analysis = analyst.analyze(item)
            if not analysis:
                continue

            obsidian.write_analysis(analysis)

            bt_result = backtester.validate(analysis)
            obsidian.write_backtest_result(analysis, bt_result)
            if not bt_result.passed:
                logger.info(f"Backtest FAILED for {analysis.question[:40]}: {bt_result.details}")
                continue

            decision = strategist.decide(analysis, bt_result)
            obsidian.write_decision(decision, analysis)

            if decision.action == "pass":
                logger.info(f"Strategist: PASS — {decision.reasoning}")
                continue

            market = clients_dict[analysis.platform].get_market(analysis.market_id)
            executor.execute(decision, market)
            working.remove_from_watchlist(analysis.market_id)
            traded += 1

            logger.info(
                f"Executed: {decision.action} {analysis.question[:50]} "
                f"@ {decision.target_price:.3f} (${decision.size_usd:.2f})"
            )
        except Exception as e:
            logger.error(f"Analysis failed for {item.market_id}: {e}", exc_info=True)

    logger.info(f"Analysis cycle complete — {traded}/{len(top_items)} traded")


def review_task() -> None:
    """Review loop — check outcomes, extract learnings, write dashboard."""
    logger.info(f"[{datetime.now(timezone.utc).isoformat()}] Review cycle started")
    try:
        working.reset_daily_tracking()  # reset circuit breaker + daily loss baseline
        stats = reviewer.run()
        obsidian.write_daily_review(stats)
        obsidian.write_patterns()
        obsidian.write_excalidraw_portfolio()
        logger.info(f"Review complete — bankroll=${stats['bankroll']:.2f}")
    except Exception as e:
        logger.error(f"Review cycle failed: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Scheduler setup
# ---------------------------------------------------------------------------

sched = BackgroundScheduler()

# Scanner: every N hours
sched.add_job(scan_task, "interval", hours=SCAN_INTERVAL_HOURS, id="scanner")

# Analysis: every N hours
sched.add_job(analysis_task, "interval", hours=ANALYSIS_INTERVAL_HOURS, id="analyst")

# Review: daily at REVIEW_HOUR_UTC
sched.add_job(review_task, "cron", hour=REVIEW_HOUR_UTC, id="reviewer")

sched.start()

logger.info(
    "Autonomous Prediction Market Alpha System started\n"
    f"  Scanner:  every {SCAN_INTERVAL_HOURS}h\n"
    f"  Analyst:  every {ANALYSIS_INTERVAL_HOURS}h\n"
    f"  Reviewer: daily at {REVIEW_HOUR_UTC:02d}:00 UTC\n"
    f"  Bankroll: ${working.bankroll:.2f}\n"
    f"  Watchlist: {len(working.watchlist)} markets\n"
    f"  Positions: {len(working.open_positions())} open"
)

# Run first scan + analysis immediately on startup
scan_task()
analysis_task()

while True:
    time.sleep(60)
