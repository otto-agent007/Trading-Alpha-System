# Trading Alpha System

Autonomous prediction market research system that continuously searches for pricing inefficiencies on [Polymarket](https://polymarket.com) and [Kalshi](https://kalshi.com).

## How It Works

The system runs a research loop three times daily (00:00, 08:00, 16:00 UTC):

1. **DataEngineer** — fetches resolved and open markets from the Polymarket API
2. **HypothesisGenerator** — uses an LLM to propose a new market strategy (category, entry probability range, direction)
3. **BacktestEngineer** — tests the hypothesis against historical resolved markets, calculates win rate and expected value
4. **RiskManager** — accepts strategies with EV > 3%, sample size ≥ 10, and win rate > 50%; calculates quarter-Kelly position sizing
5. **ObsidianOrchestrator** — writes results to an Obsidian vault as Markdown dashboards and Quarto reports
6. **HybridMemory** — stores every episode in ChromaDB so future hypotheses avoid repeating tested strategies

At 03:00 UTC, a nightly consolidation job summarizes past results by category and writes a `Weekly_Summary.md` to the vault.

## Stack

- **LLM**: OpenRouter (Claude 4.6 Opus) with local Ollama fallback
- **Memory**: ChromaDB + `all-MiniLM-L6-v2` sentence embeddings
- **Data**: Polymarket Gamma API (public, no auth)
- **Reports**: Quarto `.qmd` + Plotly
- **Scheduler**: APScheduler
- **Container**: Docker + Docker Compose

## Quick Start

### 1. Set environment variables

Copy and fill in:

```bash
OPENROUTER_API_KEY=sk-or-...
API_FALLBACK_ENABLED=true
OBSIDIAN_VAULT=/path/to/your/obsidian/vault
```

### 2. Run with Docker Compose

```bash
docker compose up -d
```

This starts:
- `ollama` service (local LLM, pulls `qwen3.5:4b` on first run)
- `alpha-app` (the research loop)

### 3. Run directly (Ollama already running)

```bash
pip install -r requirements.txt
python main.py
```

## GitHub Actions

On every push to `main`, the workflow in [.github/workflows/docker.yml](.github/workflows/docker.yml) builds the Docker image and pushes it to GitHub Container Registry (`ghcr.io`).

### Required Secrets

Set these in **GitHub → Settings → Secrets and variables → Actions**:

| Secret | Description |
|--------|-------------|
| `OPENROUTER_API_KEY` | Your OpenRouter API key (for your favorite LLM) |

`GITHUB_TOKEN` is provided automatically by GitHub Actions for the container registry push.

## Output

Results are written to your Obsidian vault under `Alpha Research/Dashboard/`:

- `Cycle_{id}.md` — per-cycle dashboard with metrics and matching live markets
- `Report_{id}.qmd` — Quarto report with Plotly visualisation
- `Weekly_Summary.md` — nightly LLM consolidation of all past episodes

## Architecture

```
core/
  pydantic_ai.py      # MarketHypothesis, MarketEdgeMetrics, MarketVerdict, BaseAgent
  model_router.py     # OpenRouter + Ollama hybrid routing
  hybrid_memory.py    # ChromaDB episodic memory

agents/
  data_engineer.py        # Polymarket API fetcher
  hypothesis_generator.py # LLM strategy proposer
  backtest_engineer.py    # Historical market evaluator
  risk_manager.py         # Kelly sizing + acceptance criteria
  obsidian_orchestrator.py # Vault writer

backtesting/
  engine_stocks.py    # Archived: original vectorbt/SPY engine
```
