import logging
import time
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler

from agents.data_engineer import DataEngineer
from agents.hypothesis_generator import HypothesisGenerator
from agents.backtest_engineer import BacktestEngineer
from agents.risk_manager import RiskManager
from agents.obsidian_orchestrator import ObsidianOrchestrator
from core.hybrid_memory import memory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def alpha_search_task() -> None:
    logger.info(f"[{datetime.now()}] Alpha cycle started")

    try:
        # 1. Fetch fresh market data from Polymarket
        historical_df, open_df = DataEngineer.fetch()

        # 2. Query memory for past strategies to avoid duplication
        past_episodes = memory.query("prediction market hypothesis", n=5)

        # 3. Generate a new hypothesis via LLM
        generator = HypothesisGenerator()
        sample = historical_df.sample(min(10, len(historical_df))) if not historical_df.empty else historical_df
        hypothesis = generator.generate(past_episodes, sample)
        logger.info(
            f"Hypothesis: {hypothesis.category} {hypothesis.position} "
            f"@ {hypothesis.prob_range[0]:.0%}–{hypothesis.prob_range[1]:.0%} "
            f"({hypothesis.days_to_resolution[0]}–{hypothesis.days_to_resolution[1]} days)"
        )

        # 4. Backtest against historical resolved markets
        metrics = BacktestEngineer.evaluate(hypothesis, historical_df)

        # 5. Risk assessment + acceptance decision
        verdict = RiskManager.assess(metrics, hypothesis)

        # 6. Write Obsidian dashboard + store in ChromaDB
        ObsidianOrchestrator.write_dashboard(hypothesis, metrics, verdict, open_df)

        logger.info(
            f"Cycle complete — accepted={verdict.accepted}, "
            f"EV={verdict.expected_value:.4f}, kelly={verdict.kelly_fraction:.3f}"
        )

    except Exception as e:
        logger.error(f"Alpha cycle failed: {e}", exc_info=True)


scheduler = BackgroundScheduler()
scheduler.add_job(alpha_search_task, "cron", hour="0,8,16")
scheduler.add_job(memory.consolidate, "cron", hour=3)
scheduler.start()

logger.info("Autonomous Prediction Market Alpha System started (Polymarket + LLM + ChromaDB)")

# Run one cycle immediately on startup
alpha_search_task()

while True:
    time.sleep(60)
