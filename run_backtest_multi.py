"""Backtest multi-actifs — simule la sélection dynamique (top longs/shorts)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
import numpy as np
import requests
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv()

from src.config import Settings
from src.data.yfinance_client import fetch_ohlcv
from src.strategy.momentum_sentiment import MomentumSentimentStrategy
from src.strategy.config_builder import build_strategy_config

console = Console()

TEST_SYMBOLS = [
    "BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD",
    "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD",
    "MATIC-USD", "NEAR-USD",
]


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
        console.log(f"[yellow]Fear&Greed indisponible: {e}[/]")
        return None


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2024-06-01")
    parser.add_argument("--interval", default="1h")
    parser.add_argument("--max-positions", type=int, default=4)
    parser.add_argument("--symbols", nargs="+", default=TEST_SYMBOLS)
    parser.add_argument("--fee-bps", type=float, default=10)
    parser.add_argument("--slippage-bps", type=float, default=5)
    args = parser.parse_args()

    settings = Settings.load(path=args.config) if args.config else Settings.load()
    # Config centralisée: même paramètres qu'en live (cf config_builder.py),
    # au lieu d'un 3e jeu de défauts qui divergeait des deux autres scripts.
    sc = build_strategy_config(settings.raw)
    strat = MomentumSentimentStrategy(sc)
    max_pos = args.max_positions
    cost_frac = (args.fee_bps + args.slippage_bps) / 1e4

    fg_series = fetch_fear_greed_history()

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

    # 2. Aligner sur l'index du plus gros marché (BTC)
    ref_idx = all_data["BTC-USD"].index if "BTC-USD" in all_data else sorted(set.union(*(set(df.index) for df in all_data.values())))
    console.log(f"[dim]{len(ref_idx)} barres de référence[/]")

    if len(ref_idx) < 20:
        console.print("[red]Pas assez de barres.[/]")
        return

    # 3. Pré-calculer scores ET prix, tous deux ffill sur ref_idx.
    #    (avant: seuls les scores étaient ffillés, pas les prix de clôture ->
    #    un reindex direct sur le Close produisait des NaN et faisait
    #    disparaître des positions silencieusement lors de la fermeture)
    signals: dict[str, pd.Series] = {}
    closes: dict[str, pd.Series] = {}
    for sym, df in all_data.items():
        sig = strat.vectorized_signals(df, sentiment_series=None, fear_greed_series=fg_series)
        signals[sym] = sig["score"].reindex(ref_idx, method="ffill").fillna(0)
        closes[sym] = df["Close"].reindex(ref_idx, method="ffill")
    console.log(f"[dim]{len(signals)} séries de signaux alignées[/]")

    # 4. Simuler la sélection et le P&L
    initial_capital = 100.0
    capital = initial_capital
    equity_curve = [capital]
    trades = 0
    wins = 0
    losses = 0
    open_positions: dict[str, dict] = {}  # sym -> {entry_price, side, qty, notional}
    half = max(1, max_pos // 2)
    alloc_frac = 0.80 / max_pos  # fraction du capital par slot, dérivée de --max-positions

    for t in range(1, len(ref_idx)):
        ts = ref_idx[t]
        scored = [(signals[sym].iloc[t - 1], sym) for sym in signals]
        scored.sort(key=lambda x: x[0])

        shorts = [s for s in scored if s[0] < sc.threshold_short][:half] if sc.allow_short else []
        longs = [s for s in reversed(scored) if s[0] > sc.threshold_long][:half]
        interleaved = []
        for i in range(max(len(shorts), len(longs))):
            if i < len(shorts):
                interleaved.append(shorts[i])
            if i < len(longs):
                interleaved.append(longs[i])
        selected = interleaved[:max_pos]
        selected_symbols = {sym for _, sym in selected}

        # Fermer les positions qui ne sont plus sélectionnées.
        # On vérifie le prix AVANT de retirer la position: si absent, on la
        # garde ouverte plutôt que de la perdre silencieusement.
        for sym in list(open_positions.keys()):
            if sym in selected_symbols or sym not in closes:
                continue
            exit_price = closes[sym].loc[ts]
            if pd.isna(exit_price) or exit_price <= 0:
                continue  # position conservée, on retentera à t+1
            pos = open_positions.pop(sym)
            pnl_gross = (exit_price - pos["entry_price"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
            fee = pos["notional"] * cost_frac
            pnl = pnl_gross - fee
            capital += pnl
            wins += pnl > 0
            losses += pnl <= 0
            trades += 1

        # Ouvrir les nouvelles positions
        for score, sym in selected:
            if sym in open_positions or sym not in closes:
                continue
            entry_price = closes[sym].loc[ts]
            if pd.isna(entry_price) or entry_price <= 0:
                continue
            side = "long" if score > 0 else "short"
            if side == "short" and not sc.allow_short:
                continue
            conviction = max(0.5, min(1.5, abs(score) / 0.10)) if score else 1.0
            notional = capital * alloc_frac * conviction
            qty = notional / entry_price
            entry_fee = notional * cost_frac
            capital -= entry_fee
            open_positions[sym] = {
                "entry_price": entry_price, "side": side, "qty": qty,
                "notional": notional, "score": score,
            }

        equity_curve.append(capital)

    # Fermer les positions restantes au dernier prix connu
    for sym, pos in list(open_positions.items()):
        exit_price = closes[sym].iloc[-1] if sym in closes else None
        if exit_price is None or pd.isna(exit_price) or exit_price <= 0:
            continue
        pnl_gross = (exit_price - pos["entry_price"]) * pos["qty"] * (1 if pos["side"] == "long" else -1)
        fee = pos["notional"] * cost_frac
        pnl = pnl_gross - fee
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
    table.add_row("Fees+slippage", f"{(args.fee_bps + args.slippage_bps):.0f} bps/trade")
    table.add_row("Période", f"{args.start} → {args.end}")
    table.add_row("Max positions", str(max_pos))
    table.add_row("Shorts autorisés", str(sc.allow_short))
    console.print(table)
    console.log(f"[dim]Capital final: ${capital:.2f} (départ: ${initial_capital:.2f})[/]")


if __name__ == "__main__":
    main()