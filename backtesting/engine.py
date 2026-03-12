import subprocess
import json
from pathlib import Path
from config import DATA_PATH

def run_vectorbt_safely(hyp_id: str):
    # Build the backtest script line by line (safe for chat rendering)
    script_lines = [
        "import vectorbt as vbt",
        "import yfinance as yf",
        "import pandas as pd",
        "import json",
        "",
        "df = yf.download('SPY', period='2y', progress=False)",
        "price = df['Close']",
        "fast_ma = vbt.MA.run(price, window=20)",
        "slow_ma = vbt.MA.run(price, window=50)",
        "entries = fast_ma.ma_crossed_above(slow_ma)",
        "exits = fast_ma.ma_crossed_below(slow_ma)",
        "",
        "pf = vbt.Portfolio.from_signals(price, entries, exits, init_cash=10000)",
        "",
        "stats = {",
        '    "sharpe": float(pf.sharpe_ratio),',
        '    "max_dd": float(pf.max_drawdown),',
        '    "total_return": float(pf.total_return)',
        "}",
        "",
        "print(json.dumps(stats))"
    ]
    script = "\n".join(script_lines)

    try:
        result = subprocess.run(
            ["python", "-c", script],
            capture_output=True,
            text=True,
            timeout=45,
            cwd=str(DATA_PATH)
        )
        return json.loads(result.stdout.strip())
    except Exception as e:
        print(f"Backtest error: {e}")
        return {"sharpe": 0.0, "max_dd": 0.0}