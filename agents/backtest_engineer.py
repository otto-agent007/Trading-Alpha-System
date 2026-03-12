import logging
import pandas as pd
from core.pydantic_ai import MarketHypothesis, MarketEdgeMetrics

logger = logging.getLogger(__name__)

MIN_SAMPLE_SIZE = 10


class BacktestEngineer:
    @staticmethod
    def evaluate(
        hypothesis: MarketHypothesis,
        historical_df: pd.DataFrame,
    ) -> MarketEdgeMetrics:
        """
        Test a MarketHypothesis against historical resolved Polymarket markets.

        Strategy: filter resolved markets by category, volume, and days_active,
        then calculate win rate and expected value assuming entry at the midpoint
        of hypothesis.prob_range.

        Returns MarketEdgeMetrics with sample_size=0 if not enough data.
        """
        _empty = MarketEdgeMetrics(
            win_rate=0.0,
            expected_value=0.0,
            sample_size=0,
            avg_entry_prob=(hypothesis.prob_range[0] + hypothesis.prob_range[1]) / 2,
        )

        if historical_df is None or historical_df.empty:
            logger.warning("BacktestEngineer: empty historical dataset")
            return _empty

        df = historical_df.copy()

        # --- Filters ---
        # Category
        df = df[df["category"].str.lower() == hypothesis.category.lower()]

        # Volume
        df = df[df["volume_usd"] >= hypothesis.min_volume_usd]

        # Days to resolution
        if "days_active" in df.columns:
            min_days, max_days = hypothesis.days_to_resolution
            df = df[df["days_active"].between(min_days, max_days, inclusive="both")]

        sample_size = len(df)

        if sample_size < MIN_SAMPLE_SIZE:
            logger.info(
                f"BacktestEngineer: only {sample_size} matches for "
                f"category={hypothesis.category}, min required={MIN_SAMPLE_SIZE}"
            )
            return MarketEdgeMetrics(
                win_rate=0.0,
                expected_value=0.0,
                sample_size=sample_size,
                avg_entry_prob=(hypothesis.prob_range[0] + hypothesis.prob_range[1]) / 2,
            )

        # Assumed entry price = midpoint of the prob_range in the hypothesis
        avg_entry_prob = (hypothesis.prob_range[0] + hypothesis.prob_range[1]) / 2

        # Win = market resolved in the direction we bet
        if hypothesis.position == "YES":
            wins = int(df["resolved_yes"].sum())
        else:
            wins = int((~df["resolved_yes"]).sum())

        win_rate = wins / sample_size

        # EV per dollar risked at avg_entry_prob
        # Buy YES at price p: profit = (1 - p) if win, loss = p if lose
        ev = win_rate * (1 - avg_entry_prob) - (1 - win_rate) * avg_entry_prob

        logger.info(
            f"BacktestEngineer: n={sample_size}, win_rate={win_rate:.2%}, "
            f"EV={ev:.4f}, avg_entry_prob={avg_entry_prob:.3f}"
        )

        return MarketEdgeMetrics(
            win_rate=float(win_rate),
            expected_value=float(ev),
            sample_size=int(sample_size),
            avg_entry_prob=float(avg_entry_prob),
        )
