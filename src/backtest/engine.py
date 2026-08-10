"""Moteur de backtest vectorisé — simule une stratégie sur un DataFrame OHLCV."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..strategy.momentum_sentiment import MomentumSentimentStrategy


@dataclass
class BacktestResult:
    total_return: float
    sharpe:       float
    max_dd:       float
    trades:       int
    win_rate:     float
    equity:       pd.Series


def run(df: pd.DataFrame, strat: MomentumSentimentStrategy, cost_bps: float = 10) -> BacktestResult:
    """Exécute un backtest vectorisé sur *df* avec la stratégie *strat*.

    Paramètres
    ----------
    df : pd.DataFrame
        OHLCV avec index DateTime et colonnes Open/High/Low/Close/Volume.
    strat : MomentumSentimentStrategy
        Stratégie (vectorized_signals est appelée).
    cost_bps : float
        Frais de trading en points de base (10 = 0.1%).

    Retourne
    -------
    BacktestResult avec total_return, sharpe, max_dd, trades, win_rate, equity.
    """
    sig = strat.vectorized_signals(df)
    position = sig["position"]
    close = df["Close"]

    cost_frac = cost_bps / 1e4

    # Rendements quotidiens de la position
    returns = close.pct_change()
    strategy_returns = position.shift(1) * returns

    # Frais : on les déduit quand la position change (entry ou exit)
    position_changes = position.diff().abs().fillna(0)
    fees = position_changes * cost_frac
    strategy_returns -= fees

    equity = (1 + strategy_returns).cumprod()
    total_return = float(equity.iloc[-1]) - 1.0 if not equity.empty else 0.0

    # Sharpe annualisé (365j × 24h pour données horaires)
    ret_series = strategy_returns.dropna()
    if len(ret_series) > 1:
        sharpe = float(np.sqrt(365 * 24) * ret_series.mean() / max(ret_series.std(), 1e-6))
    else:
        sharpe = 0.0

    # Max drawdown
    peak = equity.cummax()
    dd = ((equity - peak) / peak).min()
    max_dd = float(dd) if not pd.isna(dd) else 0.0

    # Trades et win rate
    trades = int((position.diff().abs() > 0).sum())
    if trades > 0:
        # P&L par trade
        trade_pnls = []
        in_position = False
        entry_equity = 1.0
        for i in range(len(equity)):
            if position.iloc[i] != 0 and not in_position:
                entry_equity = equity.iloc[i - 1] if i > 0 else 1.0
                in_position = True
            elif position.iloc[i] == 0 and in_position:
                trade_pnl = equity.iloc[i] / entry_equity - 1
                trade_pnls.append(trade_pnl)
                in_position = False
        # Dernier trade si encore en position
        if in_position:
            trade_pnl = equity.iloc[-1] / entry_equity - 1
            trade_pnls.append(trade_pnl)
        wins = sum(1 for p in trade_pnls if p > 0)
        win_rate = float(wins / max(len(trade_pnls), 1))
    else:
        win_rate = 0.0

    return BacktestResult(
        total_return=total_return,
        sharpe=sharpe,
        max_dd=max_dd,
        trades=trades,
        win_rate=win_rate,
        equity=equity,
    )