"""Backtest multi-actifs — simule la sélection dynamique (top longs/shorts)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv()

from src.config import Settings
from src.data.yfinance_client import fetch_ohlcv
from src.strategy.momentum_sentiment import MomentumSentimentStrategy, StrategyConfig
from src.backtest.engine import run as backtest_single
from src.strategy import indicators as ind

console = Console()

# Symboles tests — top 10 par capitalisation + quelques mid-caps
TEST_SYMBOLS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
    "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD",
    "MATIC-USD", "NEAR-USD",
]

def load_config(config_path: str | None = None) -> StrategyConfig:
    settings = Settings.load(path=config_path) if config_path else Settings.load()
    cfg = settings.raw.get("strategy", {})
    weights = cfg.get("weights", {})
    mom = cfg.get("momentum", {})
    thresh = cfg.get("thresholds", {})
    sent = cfg.get("sentiment", {})
    risk = settings.raw.get("risk", {})
    return StrategyConfig(
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
        enable_trend_filter=mom.get("enable_trend_filter", True),
        sma_fast=mom.get("sma_fast", 24),
        sma_slow=mom.get("sma_slow", 144),
    )

def compute_signals(df: pd.DataFrame, strat: MomentumSentimentStrategy) -> pd.Series:
    """Calcule les signaux vectorisés pour un symbole."""
    sig = strat.vectorized_signals(df)
    return sig["score"] if "score" in sig.columns else sig["position"] * 0.0

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-06-01")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--max-positions", type=int, default=4)
    parser.add_argument("--symbols", nargs="+", default=TEST_SYMBOLS)
    args = parser.parse_args()

    sc = load_config()
    strat = MomentumSentimentStrategy(sc)
    max_pos = args.max_positions

    # 1. Charger tous les symboles
    all_data: dict[str, pd.DataFrame] = {}
    errors = 0
    for sym in args.symbols:
        try:
            df = fetch_ohlcv(sym, start=args.start, end=args.end, interval=args.interval)
            if df.empty or len(df) < 50:
                console.log(f"[yellow]{sym}: données insuffisantes ({len(df)} barres)[/]")
                errors += 1
                continue
            all_data[sym] = df
            console.log(f"[dim]{sym}: {len(df)} barres[/]")
        except Exception as e:
            console.log(f"[red]{sym}: {e}[/]")
            errors += 1

    if len(all_data) < 2:
        console.print("[red]Pas assez de données pour le backtest multi-actifs.[/]")
        return

    console.log(f"[green]{len(all_data)} symboles chargés ({errors} erreurs)[/]")

    # 2. Aligner sur l'index du plus gros marché (BTC) pour gérer les actifs récents
    ref_idx = all_data["BTC-USD"].index if "BTC-USD" in all_data else sorted(set.union(*(set(df.index) for df in all_data.values())))
    console.log(f"[dim]{len(ref_idx)} barres de référence[/]")

    if len(ref_idx) < 20:
        console.print("[red]Pas assez de barres.[/]")
        return

    # 3. Pré-calculer les signaux pour chaque symbole et aligner sur ref_idx
    signals: dict[str, pd.Series] = {}
    for sym, df in all_data.items():
        sig = strat.vectorized_signals(df)
        # Forward-fill depuis les données dispo, combler les débuts par 0
        signals[sym] = sig["score"].reindex(ref_idx, method="ffill").fillna(0)
    console.log(f"[dim]{len(signals)} séries de signaux alignées[/]")

    # 4. Simuler la sélection et le P&L
    initial_capital = 100.0
    capital = initial_capital
    equity_curve = [capital]
    trades = 0
    wins = 0
    losses = 0
    open_positions: dict[str, dict] = {}  # sym -> {entry_price, side, qty}
    half = max(1, max_pos // 2)

    for t in range(1, len(ref_idx)):
        ts = ref_idx[t]
        # Scores du jour t-1 (lag pour éviter le forward bias)
        scored = [(signals[sym].iloc[t-1], sym) for sym in signals]
        scored.sort(key=lambda x: x[0])

        # Sélection entrelacée (même logique que step())
        shorts = [s for s in scored if s[0] < sc.threshold_short][:half]
        longs = [s for s in reversed(scored) if s[0] > sc.threshold_long][:half]
        interleaved = []
        for i in range(max(len(shorts), len(longs))):
            if i < len(shorts):
                interleaved.append(shorts[i])
            if i < len(longs):
                interleaved.append(longs[i])
        selected = interleaved[:max_pos]

        # Fermer les positions qui ne sont plus sélectionnées
        selected_symbols = {sym for _, sym in selected}
        for sym in list(open_positions.keys()):
            if sym not in selected_symbols and sym in all_data:
                pos = open_positions.pop(sym)
                exit_price = all_data[sym]["Close"].reindex([ts]).iloc[0]
                if pd.isna(exit_price):
                    continue
                pnl_pct = (exit_price / pos["entry_price"] - 1) * (1 if pos["side"] == "long" else -1)
                pnl = capital * 0.25 * pnl_pct  # ~1/max_pos du capital
                capital += pnl
                wins += pnl > 0
                losses += pnl <= 0
                trades += 1

        # Ouvrir les nouvelles positions
        for score, sym in selected:
            if sym in open_positions or sym not in all_data:
                continue
            df = all_data[sym]
            entry_price = df["Close"].reindex([ts]).iloc[0]
            if pd.isna(entry_price) or entry_price <= 0:
                continue
            side = "long" if score > 0 else "short"
            # Conviction sizing (adapté de _position_size)
            conviction = max(0.5, min(1.5, abs(score) / 0.10)) if score else 1.0
            qty = (capital * 0.80 / max_pos * conviction) / entry_price
            open_positions[sym] = {"entry_price": entry_price, "side": side, "qty": qty, "score": score}

        equity_curve.append(capital)

    # Fermer les positions restantes
    for sym, pos in list(open_positions.items()):
        df = all_data.get(sym)
        if df is None:
            continue
        exit_price = df["Close"].iloc[-1]
        pnl_pct = (exit_price / pos["entry_price"] - 1) * (1 if pos["side"] == "long" else -1)
        pnl = capital * 0.25 * pnl_pct
        capital += pnl
        wins += pnl > 0
        losses += pnl <= 0
        trades += 1

    # Métriques
    total_ret = capital / initial_capital - 1
    equity_series = pd.Series(equity_curve)
    returns = equity_series.pct_change().dropna()
    sharpe = float(np.sqrt(365 * 24) * returns.mean() / max(returns.std(), 1e-6)) if len(returns) > 1 else 0.0
    peak = equity_series.cummax()
    dd = ((equity_series - peak) / peak).min()
    win_rate = wins / max(trades, 1)

    table = Table(title=f"Backtest Multi-Actifs ({len(all_data)} symboles)")
    table.add_column("Metric"); table.add_column("Value")
    table.add_row("Symboles testés", str(len(all_data)))
    table.add_row("Total return", f"{total_ret:+.2%}")
    table.add_row("Sharpe (annualized)", f"{sharpe:.2f}")
    table.add_row("Max drawdown", f"{dd:.2%}")
    table.add_row("Trades", str(trades))
    table.add_row("Win rate", f"{win_rate:.2%}")
    table.add_row("Période", f"{args.start} → {args.end}")
    table.add_row("Max positions", str(max_pos))
    console.print(table)

    # Top 3 symboles les plus tradés
    console.log(f"[dim]Capital final: ${capital:.2f} (départ: ${initial_capital:.2f})[/]")


if __name__ == "__main__":
    main()