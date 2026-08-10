"""Lance un backtest vectorisé sur yfinance."""
from __future__ import annotations

import sys
from pathlib import Path

import click
import pandas as pd
import requests
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(Path(__file__).parent))

from src.backtest.engine import run
from src.config import Settings
from src.data.yfinance_client import fetch_ohlcv
from src.strategy.config_builder import build_strategy_config
from src.strategy.momentum_sentiment import MomentumSentimentStrategy


def fetch_fear_greed_history() -> pd.Series | None:
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=0&format=json", timeout=15)
        r.raise_for_status()
        df = pd.DataFrame(r.json()["data"])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        df["value"] = df["value"].astype(float)
        df = df.set_index("timestamp").sort_index()
        return ((df["value"] / 50.0) - 1.0).rename("fear_greed")
    except Exception as e:
        print(f"[fear&greed] échec récupération: {e}", file=sys.stderr)
        return None


@click.command()
@click.option("--config", default=None, help="Fichier YAML de config alternatif")
@click.option("--symbol", "symbols", multiple=True, default=["BTC-USD"], help="Répétable: --symbol BTC-USD --symbol ETH-USD")
@click.option("--start", default="2023-01-01")
@click.option("--end", default=None)
@click.option("--interval", default="1h")
def main(config: str | None, symbols: tuple[str, ...], start: str, end: str | None, interval: str) -> None:
    console = Console()
    interval_cryptocom = "1d" if interval == "1day" else interval

    settings = Settings.load(path=config) if config else Settings.load()
    # Config centralisée: garantit que le backtest utilise EXACTEMENT les mêmes
    # paramètres (close_threshold, sma_fast/slow, min_momentum_abs, etc.) que
    # trading_agent.py en live. Ne plus construire StrategyConfig() à la main ici.
    sc = build_strategy_config(settings.raw)
    strat = MomentumSentimentStrategy(sc)

    fg_series = fetch_fear_greed_history()
    if fg_series is None:
        console.print("[yellow]Fear&Greed indisponible → composante neutralisée (0.0) pour ce run[/]")

    table = Table(title="Backtest — résultats par symbole")
    table.add_column("Symbol"); table.add_column("Trades"); table.add_column("Win rate")
    table.add_column("Return"); table.add_column("Sharpe"); table.add_column("Max DD")

    for symbol in symbols:
        console.log(f"Fetching {symbol} {interval} from {start} to {end or 'now'}...")
        df = fetch_ohlcv(symbol, start=start, end=end, interval=interval_cryptocom)
        if df.empty:
            console.print(f"[red]{symbol}: aucune donnée, ignoré.[/]")
            continue
        console.log(f"{symbol}: {len(df)} bars loaded.")

        # NOTE: nécessite que run() dans engine.py accepte et transmette
        # fear_greed_series à strategy.vectorized_signals() — cf patch séparé.
        res = run(df, strat, fear_greed_series=fg_series)

        out = Path("data") / f"backtest_{symbol.replace('-', '')}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        res.equity.to_csv(out, header=["equity"])

        table.add_row(
            symbol, str(res.trades), f"{res.win_rate:.2%}",
            f"{res.total_return:+.2%}", f"{res.sharpe:.2f}", f"{res.max_dd:.2%}",
        )
        console.log(f"{symbol}: equity curve → {out}")

    console.print(table)


if __name__ == "__main__":
    main()