import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
OBSIDIAN_VAULT = Path(os.getenv("OBSIDIAN_VAULT", "/app/obsidian_vault"))
MEMORY_PATH = Path(os.getenv("MEMORY_PATH", "/app/memory"))
DATA_PATH = Path(os.getenv("DATA_PATH", "/app/data"))

# ---------------------------------------------------------------------------
# LLM — tiered routing
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
HEAVY_MODEL = os.getenv("HEAVY_MODEL", "anthropic/claude-opus-4-6")
LIGHT_MODEL = os.getenv("LIGHT_MODEL", "qwen3.5:4b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")  # optional — Gemini Embedding

# ---------------------------------------------------------------------------
# Platform API keys
# ---------------------------------------------------------------------------
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
# Live trading credentials — leave blank to stay in paper mode.
# POLYMARKET_PRIVATE_KEY: EVM private key (hex, with or without 0x prefix).
# POLYMARKET_ADDRESS: EVM wallet address corresponding to the private key.
# Also requires PAPER_MODE=false and working.live_mode_enabled=True.
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_ADDRESS = os.getenv("POLYMARKET_ADDRESS", "")
KALSHI_API_KEY = os.getenv("KALSHI_API_KEY", "")
KALSHI_API_SECRET = os.getenv("KALSHI_API_SECRET", "")

# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() == "true"
INITIAL_BANKROLL = float(os.getenv("INITIAL_BANKROLL", "1000"))
MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.05"))

# ---------------------------------------------------------------------------
# Schedule (hours UTC)
# ---------------------------------------------------------------------------
SCAN_INTERVAL_HOURS = int(os.getenv("SCAN_INTERVAL_HOURS", "2"))
ANALYSIS_INTERVAL_HOURS = int(os.getenv("ANALYSIS_INTERVAL_HOURS", "4"))
REVIEW_HOUR_UTC = int(os.getenv("REVIEW_HOUR_UTC", "3"))
ANALYSIS_BATCH_SIZE = int(os.getenv("ANALYSIS_BATCH_SIZE", "5"))

# ---------------------------------------------------------------------------
# Auto-discovery keywords (comma-separated)
# Scanner will flag markets matching these without needing Ollama.
# ---------------------------------------------------------------------------
_raw_kw = os.getenv("WATCH_KEYWORDS", "")
WATCH_KEYWORDS: list[str] = [k.strip() for k in _raw_kw.split(",") if k.strip()]
