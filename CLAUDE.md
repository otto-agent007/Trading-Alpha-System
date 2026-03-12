# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the System

```bash
# Start all services (app + Ollama)
docker-compose up -d

# Run the app directly (requires Ollama running separately)
python main.py

# Test Polymarket data fetch in isolation
python -c "from agents.data_engineer import DataEngineer; h, o = DataEngineer.fetch(); print(len(h), 'historical,', len(o), 'open')"

# Test hypothesis generation (requires OPENROUTER_API_KEY or Ollama running)
python -c "from agents.hypothesis_generator import HypothesisGenerator; import pandas as pd; print(HypothesisGenerator().generate([], pd.DataFrame()))"

# Query memory
python -c "from core.hybrid_memory import memory; print(memory.query('prediction market', n=3))"
```

## Environment Variables

Set these in `docker-compose.yml` or a `.env` file:
- `OLLAMA_HOST` — Ollama server URL (default: `http://ollama:11434`)
- `MODEL` — Local LLM model name (default: `qwen3.5:4b`)
- `OPENROUTER_API_KEY` — Cloud API key for Claude 3.5 Sonnet (primary LLM path)
- `API_FALLBACK_ENABLED` — `true` uses OpenRouter; `false` uses local Ollama only
- `OBSIDIAN_VAULT` — Path to Obsidian vault (default: `/app/obsidian_vault`)
- `MEMORY_PATH` — ChromaDB persistence path (default: `/app/memory`)
- `DATA_PATH` — Market data cache path (default: `/app/data`)

## Architecture

**Autonomous prediction market research loop** — runs on APScheduler at 0:00, 8:00, 16:00 UTC. One immediate cycle fires on startup. Nightly memory consolidation at 3:00 UTC.

```
main.py
  └── DataEngineer.fetch()            → Polymarket Gamma API (historical + open markets)
  └── HypothesisGenerator.generate()  → LLM proposes MarketHypothesis (structured DSL)
  └── BacktestEngineer.evaluate()     → pandas filter + EV calculation on historical data
  └── RiskManager.assess()            → Kelly sizing, EV/sample thresholds → MarketVerdict
  └── ObsidianOrchestrator.write_dashboard() → Markdown + Quarto to vault
  └── HybridMemory.add_episode()      → ChromaDB storage
```

### Core Modules (`core/`)

- **`pydantic_ai.py`** — Prediction market models: `MarketHypothesis`, `MarketEdgeMetrics`, `MarketVerdict`, `BaseAgent`. `BaseAgent._call()` instantiates `ModelRouter` fresh each call (avoids module-level circular imports).
- **`model_router.py`** — Hybrid LLM routing: OpenRouter (Claude 3.5 Sonnet) when `API_FALLBACK_ENABLED=true`, otherwise local Ollama. Always returns JSON. Temperature 0.3.
- **`hybrid_memory.py`** — ChromaDB + `all-MiniLM-L6-v2` embeddings. `query()` returns list of metadata dicts. `consolidate()` groups episodes by category, asks LLM to summarise, writes `Weekly_Summary.md` to vault.

### Agents (`agents/`)

- **`data_engineer.py`** — Paginates `gamma.polymarket.com/markets` for closed (historical) and active (open) markets. Caches to `DATA_PATH/polymarket_historical.parquet` and `polymarket_open.parquet`. No auth required.
- **`hypothesis_generator.py`** — Passes past memory episodes + market sample titles to the LLM and parses the response into a `MarketHypothesis`. Retries once on parse failure; falls back to a hardcoded crypto hypothesis.
- **`backtest_engineer.py`** — Filters `historical_df` by category, volume, and `days_active`. Computes win rate and EV using `prob_range` midpoint as assumed entry price. Requires `MIN_SAMPLE_SIZE = 10` matches.
- **`risk_manager.py`** — Accepts if `EV > 0.03`, `sample_size >= 10`, and `win_rate > 50%`. Calculates quarter-Kelly position sizing (capped at 5% of bankroll).
- **`obsidian_orchestrator.py`** — Writes per-cycle `Cycle_{id}.md` and `Report_{id}.qmd` to vault. Also writes `matches_{id}.json` of open markets that currently fit the hypothesis. Calls `memory.add_episode()`.

### Archived (`backtesting/`)

- **`engine_stocks.py`** — Original vectorbt/SPY EMA crossover engine. Not used in main pipeline. Keep for reference.

## Key Implementation Notes

- **MarketHypothesis DSL**: `prob_range` is the assumed YES entry price range (not a probability filter on open markets — though the orchestrator uses it to find matching live opportunities). `days_to_resolution` filters historical markets by their active lifetime (`end_date - start_date`).
- **EV formula**: `win_rate * (1 - entry_prob) - (1 - win_rate) * entry_prob` where `entry_prob = midpoint(prob_range)`.
- **LLM output**: `ModelRouter.chat()` enforces JSON response format. `HypothesisGenerator` and `HybridMemory.consolidate()` both expect JSON back — don't change the router to return plain text.
- **ChromaDB metadata**: must be flat primitives only (str, int, float, bool). The `add_episode()` method explicitly casts all fields.
- **No tests** currently exist in this codebase.
- Quarto must be installed in the Docker image (see `Dockerfile`) to render `.qmd` reports.
