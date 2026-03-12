import json
import logging
import pandas as pd
from core.pydantic_ai import MarketHypothesis, BaseAgent

logger = logging.getLogger(__name__)

VALID_CATEGORIES = {"crypto", "politics", "sports", "economics", "other"}

SYSTEM_PROMPT = (
    "You are a quantitative prediction market researcher specializing in finding systematic "
    "mispricings on Polymarket and Kalshi. Output ONLY valid JSON — no explanation, no markdown."
)

SCHEMA_DESCRIPTION = (
    '{\n'
    '  "category": string (one of: "crypto", "politics", "sports", "economics", "other"),\n'
    '  "prob_range": [float_lo, float_hi]  (e.g. [0.10, 0.35] — entry YES price range),\n'
    '  "days_to_resolution": [int_min, int_max]  (e.g. [3, 14] — market lifetime window),\n'
    '  "min_volume_usd": float  (e.g. 5000.0 — minimum liquidity),\n'
    '  "position": "YES" or "NO",\n'
    '  "rationale": string  (1-2 sentence explanation of the inefficiency)\n'
    '}'
)


class HypothesisGenerator(BaseAgent):
    def generate(
        self,
        memory_context: list,
        market_sample: pd.DataFrame,
    ) -> MarketHypothesis:
        """Use the LLM to propose a new prediction market hypothesis."""
        past_summaries = json.dumps(
            [
                {k: v for k, v in ep.items() if k in ("category", "prob_range", "position", "rationale", "accepted", "expected_value")}
                for ep in (memory_context or [])[:5]
            ],
            default=str,
        )

        sample_titles: list[str] = []
        if not market_sample.empty and "title" in market_sample.columns:
            sample_titles = market_sample["title"].dropna().tolist()[:10]

        user_prompt = (
            f"Past tested strategies (avoid repeating these):\n{past_summaries}\n\n"
            f"Example markets currently on Polymarket:\n{json.dumps(sample_titles)}\n\n"
            f"Propose a NEW prediction market hypothesis as JSON matching this schema exactly:\n"
            f"{SCHEMA_DESCRIPTION}\n\n"
            "Focus on a specific, testable inefficiency. "
            "prob_range values must be between 0.0 and 1.0 with lo < hi. "
            "days_to_resolution values must be positive integers with min < max."
        )

        for attempt in range(2):
            try:
                raw = self._call(user_prompt, SYSTEM_PROMPT)
                data = json.loads(raw)
                # Normalise category
                data["category"] = data.get("category", "other").lower().strip()
                if data["category"] not in VALID_CATEGORIES:
                    data["category"] = "other"
                # Ensure tuple types survive Pydantic
                data["prob_range"] = tuple(data["prob_range"])
                data["days_to_resolution"] = tuple(int(x) for x in data["days_to_resolution"])
                return MarketHypothesis(**data)
            except Exception as e:
                logger.warning(f"HypothesisGenerator attempt {attempt + 1} failed: {e}")
                if attempt == 0:
                    user_prompt = (
                        f"Your previous response could not be parsed. Error: {e}\n"
                        f"Try again. Output ONLY valid JSON matching:\n{SCHEMA_DESCRIPTION}"
                    )

        logger.error("HypothesisGenerator failed twice — using fallback hypothesis")
        return MarketHypothesis(
            category="crypto",
            prob_range=(0.10, 0.30),
            days_to_resolution=(3, 14),
            min_volume_usd=5000.0,
            position="YES",
            rationale="Fallback: crypto markets may underprice short-resolution YES outcomes near support levels.",
        )
