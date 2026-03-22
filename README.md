# Trading Alpha System

Autonomous prediction market trading system that continuously discovers pricing inefficiencies on [Polymarket](https://polymarket.com) and [Kalshi](https://kalshi.com), validates them against historical data, and executes paper (or live) trades.

## How It Works

Three independent loops run on APScheduler:

**Scanner** (every 2h, light LLM)
- Fetches markets from Polymarket and Kalshi
- Classifies via local Ollama, checks semantic memory for known patterns
- Adds qualifying markets to the watchlist (filters: volume, days-to-close, price range)

**Analysis** (every 4h, heavy LLM)
1. **Analyst** — deep-dives a watchlisted market: orderbook, price history, episodic + semantic memory → fair value estimate
2. **Backtester** — validates edge against similar resolved markets from memory (gate: EV > 2%, win rate > 50%, max drawdown < 40%, n ≥ 8; graduated bootstrap tiers for cold-start)
3. **Strategist** — quarter-Kelly position sizing; passes or skips
4. **Executor** — paper execution + episodic memory logging

**Review** (daily at 03:00 UTC, heavy LLM)
- Resolves open positions, updates PnL
- Consolidates episodic episodes → semantic patterns (learnings)
- Writes Obsidian dashboards, Excalidraw portfolio map, patterns file

Both scan and analysis run immediately on startup, then on their intervals.

## Stack

- **LLM**: OpenRouter (heavy: `claude-opus-4-6`) + local Ollama (light: `qwen3.5:4b`)
- **Memory**: ChromaDB — episodic (every decision) + semantic (learned patterns) + working (JSON, positions/watchlist)
- **Embeddings**: `all-MiniLM-L6-v2` (local); Gemini Embedding optional
- **Data**: Polymarket CLOB + Gamma APIs (no auth for reads); Kalshi REST API (key required)
- **Scheduler**: APScheduler
- **Container**: Docker + Docker Compose

## Quick Start

### 1. Set environment variables

Edit `docker-compose.yml` and fill in your keys. Minimum required:

```
OPENROUTER_API_KEY=sk-or-...
```

For Kalshi (optional — Polymarket works without it):
```
KALSHI_API_KEY=...
KALSHI_API_SECRET=...
```

### 2. Seed memory (required before first trade)

The backtester blocks all trades until there are resolved episodes in memory. Seed it first:

```bash
python scripts/seed_memory.py
```

### 3. Run with Docker Compose

```bash
docker compose up -d
```

This starts:
- `ollama` — local LLM, pulls `qwen3.5:4b` on first run
- `alpha-app` — the three-loop trading system

### 4. Run directly (Ollama already running)

```bash
pip install -r requirements.txt
python main.py
```

## Environment Variables

All have defaults. Set in `docker-compose.yml` or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | *(required)* | Heavy LLM path (analysis, learning) |
| `HEAVY_MODEL` | `anthropic/claude-opus-4-6` | OpenRouter model |
| `LIGHT_MODEL` | `qwen3.5:4b` | Local Ollama model |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama URL |
| `GOOGLE_API_KEY` | — | Optional; enables Gemini Embedding |
| `KALSHI_API_KEY` | — | Required for Kalshi endpoints |
| `KALSHI_API_SECRET` | — | Required for Kalshi endpoints |
| `POLYMARKET_API_KEY` | — | Optional (reads work without it) |
| `PAPER_MODE` | `true` | `false` to enable live trading |
| `INITIAL_BANKROLL` | `1000` | Starting capital in USD |
| `MAX_POSITION_PCT` | `0.05` | Max single position (fraction of bankroll) |
| `SCAN_INTERVAL_HOURS` | `2` | Scanner frequency |
| `ANALYSIS_INTERVAL_HOURS` | `4` | Analysis frequency |
| `REVIEW_HOUR_UTC` | `3` | Daily review hour (UTC) |
| `OBSIDIAN_VAULT` | `/app/obsidian_vault` | Path to Obsidian vault |
| `MEMORY_PATH` | `/app/memory` | ChromaDB storage path |
| `DATA_PATH` | `/app/data` | Market data cache + params.json |

## Architecture

```
main.py (APScheduler — three loops)
│
├── Scanner Loop (every 2h)
│   └── agents/scanner.py → discover + classify → watchlist
│
├── Analysis Loop (every 4h)
│   ├── agents/analyst.py    → MarketAnalysis (fair value estimate)
│   ├── agents/backtester.py → BacktestResult (historical validation gate)
│   ├── agents/strategist.py → TradeDecision (Kelly sizing, no LLM)
│   └── agents/executor.py   → paper/live execution + memory logging
│
└── Review Loop (daily 03:00 UTC)
    ├── agents/reviewer.py     → resolve positions, PnL
    ├── core/memory/consolidation.py → episodic → semantic patterns
    └── agents/obsidian.py     → dashboards, patterns, Excalidraw map

core/
  models.py           # Pydantic models: Market, Orderbook, MarketAnalysis, TradeDecision, …
  router.py           # Tiered ModelRouter: reason() → OpenRouter, classify() → Ollama
  strategy_params.py  # All tunable thresholds (SP singleton); overridable via params.json
  linux_handoff.py    # Reads research findings from Linux autoresearch box
  memory/
    episodic.py       # ChromaDB — every decision + outcome
    semantic.py       # ChromaDB — extracted patterns with confidence scores
    working.py        # JSON — positions, watchlist, bankroll (atomic save)
    consolidation.py  # Nightly: episodes → semantic patterns via heavy LLM
    embeddings.py     # Shared MiniLM singleton

clients/
  base.py             # Abstract MarketClient interface
  polymarket.py       # Polymarket CLOB + Gamma APIs
  kalshi.py           # Kalshi REST API

scripts/
  seed_memory.py      # Populate episodic memory from resolved markets (run once before first trade)
  find_markets.py     # Ad-hoc market search utility
  check_endpoints.py  # Verify API connectivity
```

## Strategy Parameters

All thresholds live in `core/strategy_params.py` and can be overridden at runtime by dropping a `params.json` into `DATA_PATH`. Restart to apply. Default values:

| Parameter | Default | Meaning |
|---|---|---|
| `bt_min_sample` | 8 | Min resolved episodes for full backtest |
| `bt_min_ev` | 0.02 | Min expected value (2%) to pass backtest |
| `bt_min_win_rate` | 0.50 | Min win rate to pass backtest |
| `bt_max_drawdown` | 0.40 | Max drawdown to pass backtest |
| `kelly_fraction` | 0.25 | Quarter-Kelly sizing |
| `max_position_pct` | 0.05 | Max position as fraction of bankroll |
| `min_edge` | 0.03 | Min edge for strategist to act |
| `min_confidence` | 0.40 | Min analyst confidence to act |

## Output (Obsidian Vault)

Written to `OBSIDIAN_VAULT/Alpha Research/`:

- `Dashboard/Analysis_*.md` — per-market analysis with YAML frontmatter for Dataview
- `Dashboard/Decision_*.md` — trade decisions
- `Dashboard/Backtest_*.md` — backtest results
- `Dashboard/Scan_*.md` — scanner cycle summaries
- `Dashboard/Review_*.md` — daily review with PnL
- `Dashboard/Patterns.md` — current semantic memory (learned rules)
- `Dashboard/Portfolio.excalidraw.md` — Excalidraw position map

## GitHub Actions

On every push to `master`, [.github/workflows/docker.yml](.github/workflows/docker.yml) builds and pushes the Docker image to GitHub Container Registry (`ghcr.io`).

### Required Secret

| Secret | Description |
|---|---|
| `OPENROUTER_API_KEY` | Your OpenRouter API key |

`GITHUB_TOKEN` is provided automatically for the registry push.
