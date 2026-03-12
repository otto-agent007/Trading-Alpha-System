import logging
from core.pydantic_ai import MarketHypothesis, MarketEdgeMetrics, MarketVerdict

logger = logging.getLogger(__name__)

MIN_EV = 0.03       # minimum expected value per dollar risked
MIN_SAMPLE = 10     # minimum historical market matches


class RiskManager:
    @staticmethod
    def assess(
        metrics: MarketEdgeMetrics,
        hypothesis: MarketHypothesis,
    ) -> MarketVerdict:
        """
        Accept or reject a hypothesis based on edge strength and sample size.
        Calculates quarter-Kelly position sizing for accepted hypotheses.
        """
        reasons: list[str] = []
        accepted = True

        # Insufficient data
        if metrics.sample_size < MIN_SAMPLE:
            accepted = False
            reasons.append(
                f"Insufficient data: {metrics.sample_size} markets matched (min {MIN_SAMPLE})"
            )

        # Edge too small
        if metrics.expected_value <= MIN_EV:
            accepted = False
            reasons.append(
                f"Edge too small: EV={metrics.expected_value:.4f} (min {MIN_EV})"
            )

        # Win rate sanity check — must be above coin flip for the bet direction
        if metrics.sample_size >= MIN_SAMPLE:
            if metrics.win_rate <= 0.50:
                accepted = False
                reasons.append(
                    f"Win rate {metrics.win_rate:.2%} does not exceed 50% for "
                    f"{hypothesis.position} position"
                )

        # Quarter-Kelly position sizing (max 5% of bankroll)
        kelly_fraction = 0.0
        if accepted and 0.0 < metrics.avg_entry_prob < 1.0:
            # Kelly formula for binary bet: f = (p*b - q) / b, where b = net odds
            b = (1.0 - metrics.avg_entry_prob) / metrics.avg_entry_prob
            kelly = (metrics.win_rate * b - (1.0 - metrics.win_rate)) / b
            kelly_fraction = min(max(kelly * 0.25, 0.0), 0.05)

        reason_str = (
            "; ".join(reasons)
            if reasons
            else (
                f"EV={metrics.expected_value:.4f}, "
                f"win_rate={metrics.win_rate:.2%}, "
                f"n={metrics.sample_size}"
            )
        )

        verdict = MarketVerdict(
            hypothesis_id=hypothesis.id,
            accepted=accepted,
            reason=reason_str,
            expected_value=metrics.expected_value,
            sample_size=metrics.sample_size,
            kelly_fraction=kelly_fraction,
        )

        logger.info(
            f"RiskManager: accepted={accepted}, kelly={kelly_fraction:.3f}, reason={reason_str}"
        )
        return verdict
