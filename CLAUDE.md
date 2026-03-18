# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the System

```bash
# Start all services (app + Ollama)
docker-compose up -d

# Run the app directly (requires Ollama running separately)
python main.py

# Test Polymarket CLOB API
python -c "from clients.polymarket import PolymarketClient; c = PolymarketClient(); print(c.list_markets(active=True, limit=5))"

# Test Kalshi API (requires KALSHI_API_KEY)
python -c "from clients.kalshi import KalshiClient; c = KalshiClient(); print(c.list_markets(active=True, limit=5))"

# Test tiered LLM router
python -c "from core.router import ModelRouter; r = ModelRouter(); print(r.classify('Will BTC hit 100k?', ['crypto','politics','sports','economics','other']))"

# Query episodic memory
python -c "from core.memory.episodic import EpisodicMemory; e = EpisodicMemory(); print(e.recall('prediction market', n=3))"

# Query semantic memory (learned patterns)
python -c "from core.memory.semantic import SemanticMemory; s = SemanticMemory(); print(s.get_all())"
```

## Environment Variables

Set these in `docker-compose.yml` or a `.env` file. All have defaults in `config.py`.

**LLM (tiered routing):**
- `OPENROUTER_API_KEY` — Required. Heavy LLM path (analysis, learning extraction).
- `HEAVY_MODEL` — OpenRouter model (default: `anthropic/claude-opus-4-6`)
- `LIGHT_MODEL` — Local Ollama model (default: `qwen3.5:4b`)
- `OLLAMA_HOST` — Ollama URL (default: `http://localhost:11434`)

**Embeddings:**
- `GOOGLE_API_KEY` — Optional. Enables Gemini Embedding for semantic memory. Falls back to MiniLM.

**Paths:**
- `OBSIDIAN_VAULT` — Obsidian vault path (default: `/app/obsidian_vault`)
- `MEMORY_PATH` — ChromaDB path (default: `/app/memory`)
- `DATA_PATH` — Market data cache (default: `/app/data`)

**Platform API keys:**
- `POLYMARKET_API_KEY` — Optional for reads, required for trading (Phase 5)
- `KALSHI_API_KEY`, `KALSHI_API_SECRET` — Required for all Kalshi endpoints

**Trading:**
- `PAPER_MODE` — `true` (default) for paper trading, `false` for live
- `INITIAL_BANKROLL` — Starting capital (default: `1000`)
- `MAX_POSITION_PCT` — Max single position as fraction of bankroll (default: `0.05`)

**Schedule:**
- `SCAN_INTERVAL_HOURS` — Scanner frequency (default: `2`)
- `ANALYSIS_INTERVAL_HOURS` — Analysis frequency (default: `4`)
- `REVIEW_HOUR_UTC` — Daily review hour (default: `3`)

## Architecture

**Three-loop autonomous system** running on APScheduler:

```
main.py (multi-loop scheduler)
│
├── Scanner Loop (every 2h, light LLM)
│   └── scanner.py → discover markets on Polymarket + Kalshi
│   └── classify, check semantic memory, add to watchlist
│
├── Analysis Loop (every 4h, heavy LLM)
│   └── analyst.py   → deep-dive: orderbook, price history, memory context → MarketAnalysis
│   └── backtester.py → validate edge against similar resolved markets → BacktestResult
│   └── strategist.py → trade decision with Kelly sizing → TradeDecision
│   └── executor.py   → paper/live execution + episodic memory logging
│
├── Review Loop (daily 03:00 UTC, heavy LLM)
│   └── reviewer.py       → resolve positions, update PnL
│   └── consolidation.py  → extract learnings: episodic → semantic memory
│   └── obsidian.py        → write dashboards, patterns, Excalidraw portfolio map
│
└── Working memory (JSON) persisted across all loops
```

### Data Layer (`clients/`)

- **`base.py`** — Abstract `MarketClient` interface: `list_markets`, `get_market`, `get_orderbook`, `get_trades`, `get_price_history`.
- **`polymarket.py`** — Polymarket CLOB API (`clob.polymarket.com`) + Gamma API for metadata. No auth for reads.
- **`kalshi.py`** — Kalshi REST API (`trading-api.kalshi.com/trade-api/v2`). Requires API key for all endpoints.

### Core (`core/`)

- **`models.py`** — All Pydantic models: `Market`, `Orderbook`, `Trade`, `MarketAnalysis`, `BacktestResult`, `TradeDecision`, `Position`, `WatchlistItem`.
- **`router.py`** — Tiered `ModelRouter`: `reason()` → OpenRouter (heavy), `extract()`/`classify()`/`summarize()` → Ollama (light). All enforce JSON output.

### Memory (`core/memory/`)

Three-tier memory architecture:
- **`episodic.py`** — ChromaDB collection `episodes`. Every decision + outcome. Embedded with local MiniLM (`all-MiniLM-L6-v2`). Methods: `record()`, `recall()`, `get_recent()`.
- **`semantic.py`** — ChromaDB collection `learnings`. Extracted patterns with confidence scores. Uses Gemini Embedding when `GOOGLE_API_KEY` is set, MiniLM fallback. Methods: `store_learning()`, `query_patterns()`, `update_confidence()`, `prune()`.
- **`working.py`** — JSON file at `DATA_PATH/working_memory.json`. Tracks positions, watchlist, bankroll. Pydantic model with `save()`/`load()`.
- **`consolidation.py`** — Nightly: gets recent episodes, asks heavy LLM to extract patterns, stores in semantic memory, prunes low-confidence rules.

### Agents (`agents/`)

- **`scanner.py`** — Fetches markets from both platforms, classifies via light LLM, checks semantic memory for pattern matches, adds to watchlist. Filters: volume, days-to-close, price range.
- **`analyst.py`** — Deep-dives into a watchlisted market: fetches orderbook + price history + trades, queries episodic + semantic memory, asks heavy LLM for fair value estimate. Produces `MarketAnalysis`.
- **`backtester.py`** — Validation gate. Finds similar resolved markets via episodic memory, simulates entry at analyst's estimated fair value. Gate: sample≥8, EV>2%, win_rate>50%, max_drawdown<40%.
- **`strategist.py`** — Trade decision with quarter-Kelly sizing. Checks edge, confidence, exposure limits. Reasoning is built mechanically (no LLM call). Produces `TradeDecision`.
- **`executor.py`** — Paper execution: records position in working memory, logs to episodic memory. Live execution: Phase 5.
- **`reviewer.py`** — Daily: checks positions for resolution, calculates PnL, triggers consolidation.
- **`obsidian.py`** — Writes Markdown with YAML frontmatter for Dataview, Quarto `.qmd` reports, Excalidraw portfolio maps.

### Archived (`backtesting/`)

- **`engine_stocks.py`** — Original vectorbt/SPY EMA crossover engine. Not used.

### Research Factory Findings (from Linux box)

The Linux always-on box runs 7 research tracks continuously. Their findings
are synced to `DATA_PATH/linux_shared/` and read by the trading system via
`core/linux_handoff.py` (`LINUX` singleton):

| File | Source track | What the trading system does with it |
|------|-------------|--------------------------------------|
| `stat_patterns_findings.json` | Track 2 | Injected into analyst prompt as evidence |
| `entry_timing_findings.json` | Track 4 | (future) scanner prioritizes markets at optimal timing |
| `scanner_filters_findings.json` | Track 5 | (future) scanner reads optimal filter config |
| `portfolio_optimizer_findings.json` | Track 7 | Strategist checks category allocation before sizing |
| `prompt_optimizer_findings.json` | Track 3 | Analyst uses the winning system prompt |
| `human_feedback.json` | Obsidian | Scanner skips/boosts categories, analyst reads notes |
| `calibration.json` | Calibration | Analyst applies calibration corrections |
| `arbitrage_alerts.json` | Price monitor | Scanner treats as highest-priority discovery |
| `fast_alerts.json` | News sentinel | Fast path triggers immediate analysis |
| `crowd_opinions.json` | Data ingestion | Analyst uses as "second opinion" anchor |

## Key Implementation Notes

- **Tiered LLM**: Heavy tasks (analysis, learning) → OpenRouter. Light tasks (classification, extraction) → local Ollama. Never use the heavy model for simple classification.
- **ChromaDB metadata**: must be flat primitives (str, int, float, bool). `episodic.record()` explicitly casts all fields.
- **Backtest gate**: No trade proceeds without a passing backtest. The backtester uses actual price timeseries, not just resolution outcomes.
- **Kelly sizing**: Always quarter-Kelly, capped at `MAX_POSITION_PCT` (5%).
- **Obsidian plugins**: All vault files use consistent YAML frontmatter for Dataview. Excalidraw `.excalidraw.md` files are valid JSON.
- **No tests** currently exist. Verification is via the one-liner commands above.
- Target runtime is **Linux/Docker**. Development environment may be Windows.
- **Centralized strategy params**: All tunable trading parameters live in `core/strategy_params.py` and are read from `DATA_PATH/params.json` at startup. Agents import `SP` instead of hardcoding constants. Drop in a new params.json and restart to apply optimized parameters from autoresearch. Falls back to original defaults if no params.json exists.
- **Shared embedder**: `core/memory/embeddings.py` provides a singleton MiniLM instance via `get_local_embedder()`. Both EpisodicMemory and SemanticMemory import from there. Never instantiate SentenceTransformer directly.
- **Atomic working memory**: `WorkingMemory.save()` uses tmp→fsync→rename. Watchlist mutations do NOT auto-save (scanner saves once at the end). Position mutations (bankroll changes) DO auto-save.
- **Router retries**: `router.reason()` retries 3× with backoff on transient errors. Auth errors are not retried. Call `router.get_usage_summary()` to log token counts.
- **Consolidation IDs**: `semantic.query_patterns()` returns `_id` in each result dict. Consolidation uses `_id` (not `id`) for `update_confidence()`.

## Autoresearch Loop (Linux box)

- Overnight optimization of strategy parameters
- Outputs winning `params.json` → apply to Windows via `apply_params.py`
- Score metric: `sharpe * sqrt(trades) * (1 - max_drawdown)`

## Known System Behaviors

- Analyst is systematically overconfident on mid-range markets
- Consolidation was broken (`update_confidence` never had valid IDs) — fixed
- Bootstrap mode trades are essentially random — seed episodic memory first
- Backtester uses outcome counting, not price history — fundamental limitation

## External Data Sources (planned)

- Metaculus API: crowd probabilities as analyst "second opinion"
- Brier.fyi PostgREST API: cross-platform linked markets for arbitrage
- Manifold API: 440K+ resolved markets for backtest seeding

## Calibration Findings

[Update this section as autoresearch reveals patterns]
