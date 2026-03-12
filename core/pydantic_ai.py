from pydantic import BaseModel, Field
from typing import Literal
from datetime import datetime


class MarketHypothesis(BaseModel):
    id: str = Field(default_factory=lambda: datetime.now().strftime("%Y%m%d_%H%M%S"))
    category: str  # "crypto", "politics", "sports", "economics", "other"
    prob_range: tuple[float, float]  # assumed YES entry price range, e.g. (0.15, 0.40)
    days_to_resolution: tuple[int, int]  # market lifetime window, e.g. (7, 30)
    min_volume_usd: float  # liquidity filter
    position: Literal["YES", "NO"]
    rationale: str  # LLM-generated explanation


class MarketEdgeMetrics(BaseModel):
    win_rate: float
    expected_value: float  # avg EV per dollar risked at avg_entry_prob
    sample_size: int  # historical markets matched
    avg_entry_prob: float  # midpoint of hypothesis prob_range


class MarketVerdict(BaseModel):
    hypothesis_id: str
    accepted: bool
    reason: str
    expected_value: float
    sample_size: int
    kelly_fraction: float = 0.0  # suggested position size as fraction of bankroll


class BaseAgent:
    def _call(self, user_prompt: str, system_prompt: str) -> str:
        from core.model_router import ModelRouter
        router = ModelRouter()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return router.chat(messages)
