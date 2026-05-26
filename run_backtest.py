"""Lance un backtest vectorisé sur yfinance."""
from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from src.backtest.engine import run
from src.config import Settings
from src.data.yfinance_client import fetch_ohlcv
from src.strategy.momentum_sentiment import (MomentumSentimentStrategy,
                                              StrategyConfig)


@click.command()
@click.option("--config", default=None, help="Fichier YAML de config alternatif")
@click.option("--symbol", default="BTC-USD")
@click.option("--start", default="2023-01-01")
@click.option("--end", default=None)
@click.option("--interval", default="1h")
def main(config: str | None, symbol: str, start: str, end: str | None, interval: str) -> None:
    console = Console()
    console.log(f"Fetching{symbol} {interval} from {start} to {end or 'now'}...")
    # Accept '1day' as an alias for '1d' (CryptoCom API expects '1d')
    interval_cryptocom = "1d" if interval == "1day" else interval
    df = fetch_ohlcv(symbol, start=start, end=end, interval=interval_cryptocom)
    if df.empty:
        console.print("[red]No data fetched.")
        return
    console.log(f"{len(df)} bars loaded.")

    # Charger la config depuis YAML (fichier alternatif si fourni)
    settings = Settings.load(path=config) if config else Settings.load()
    cfg = settings.raw.get("strategy", {})
    weights = cfg.get("weights", {})
    mom = cfg.get("momentum", {})
    thresh = cfg.get("thresholds", {})
    sent = cfg.get("sentiment", {})
    risk = settings.raw.get("risk", {})

    sc = StrategyConfig(
        w_momentum=weights.get("momentum", 0.50),
        w_sentiment=weights.get("sentiment", 0.30),
        w_fear_greed=weights.get("fear_greed", 0.20),
        lookback=mom.get("lookback_days", 14),
        ema_smooth=mom.get("ema_smooth", 12),
        threshold_long=thresh.get("long", 0.20),
        threshold_short=thresh.get("short", -0.20),
        allow_short=risk.get("allow_short", False),
        high_conviction=sent.get("high_conviction", False),
        min_active_sentiment_sources=sent.get("min_active_sources", 2),
    )
    strat = MomentumSentimentStrategy(sc)
    res = run(df, strat)

    out = Path("data") / f"backtest_{symbol.replace('-', '')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    res.equity.to_csv(out, header=["equity"])

    table = Table(title=f"Backtest {symbol}")
    table.add_column("Metric"); table.add_column("Value")
    table.add_row("Total return", f"{res.total_return:+.2%}")
    table.add_row("Sharpe (annualized)", f"{res.sharpe:.2f}")
    table.add_row("Max drawdown", f"{res.max_dd:.2%}")
    table.add_row("Trades", str(res.trades))
    table.add_row("Win rate", f"{res.win_rate:.2%}")
    console.print(table)
    console.log(f"Equity curve → {out}")


if __name__ == "__main__":
    main()
