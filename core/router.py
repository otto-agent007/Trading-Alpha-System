import json
import logging
import time

from ollama import Client as OllamaClient
from openai import OpenAI

from config import (
    HEAVY_MODEL,
    LIGHT_MODEL,
    OLLAMA_HOST,
    OPENROUTER_API_KEY,
)

logger = logging.getLogger(__name__)

# Retry config for transient failures (rate limits, timeouts, 5xx)
_MAX_RETRIES = 3
_RETRY_BACKOFF = (2, 5, 10)  # seconds between retries


class ModelRouter:
    """Tiered LLM router.

    - ``reason()``   -> OpenRouter (heavy model) for analysis, planning, learning extraction
    - ``classify()`` -> Ollama (light model) for constrained category selection

    Tracks approximate OpenRouter spend so callers can log/cap it.
    """

    def __init__(self) -> None:
        self.heavy_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=OPENROUTER_API_KEY,
            timeout=60.0,      # don't hang forever on a stalled request
            max_retries=0,     # we handle retries ourselves for better logging
        )
        self.heavy_model = HEAVY_MODEL
        self.light_client = OllamaClient(host=OLLAMA_HOST)
        self.light_model = LIGHT_MODEL

        # Rough cost tracking (lifetime of this process)
        self.heavy_calls: int = 0
        self.heavy_input_tokens: int = 0
        self.heavy_output_tokens: int = 0

    # ------------------------------------------------------------------
    # Heavy path — OpenRouter (expensive, high quality)
    # ------------------------------------------------------------------

    def reason(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.4,
    ) -> str:
        """Heavy reasoning with retry on transient errors.

        Returns the raw JSON string from the model.
        Raises on persistent failure after retries.
        """
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self.heavy_client.chat.completions.create(
                    model=self.heavy_model,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                )
                content = response.choices[0].message.content or ""

                # Track usage if the API returns it
                usage = response.usage
                if usage:
                    self.heavy_input_tokens += usage.prompt_tokens or 0
                    self.heavy_output_tokens += usage.completion_tokens or 0
                self.heavy_calls += 1

                logger.debug(f"reason() -> {content[:200]}...")
                return content

            except Exception as e:
                last_exc = e
                # Don't retry on auth errors or invalid requests
                err_str = str(e).lower()
                if any(k in err_str for k in ("401", "403", "invalid", "authentication")):
                    raise

                if attempt < _MAX_RETRIES - 1:
                    wait = _RETRY_BACKOFF[attempt]
                    logger.warning(
                        f"reason() attempt {attempt + 1} failed: {e} — "
                        f"retrying in {wait}s"
                    )
                    time.sleep(wait)

        raise RuntimeError(
            f"reason() failed after {_MAX_RETRIES} attempts: {last_exc}"
        ) from last_exc

    def get_usage_summary(self) -> str:
        """Return a human-readable summary of OpenRouter usage."""
        return (
            f"OpenRouter: {self.heavy_calls} calls, "
            f"~{self.heavy_input_tokens:,} input tokens, "
            f"~{self.heavy_output_tokens:,} output tokens"
        )

    # ------------------------------------------------------------------
    # Light path — Ollama (cheap, fast)
    # ------------------------------------------------------------------

    def _ollama(
        self,
        prompt: str,
        system: str = "",
        temperature: float = 0.1,
    ) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = self.light_client.chat(
            model=self.light_model,
            messages=messages,
            format="json",
            options={"temperature": temperature},
        )
        return response.message.content or ""

    def classify(self, text: str, categories: list[str]) -> str:
        """Light: categorise text into one of the given categories.

        Returns the chosen category string (lowercased).
        """
        cats = ", ".join(categories)
        system = (
            f"You are a classifier. Respond with JSON: "
            f'{{"category": "<one of: {cats}>"}}'
        )
        raw = self._ollama(text, system, temperature=0.0)
        try:
            data = json.loads(raw)
            chosen = data.get("category", categories[-1]).lower().strip()
            return chosen if chosen in categories else categories[-1]
        except Exception:
            return categories[-1]
