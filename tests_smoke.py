"""Tests rapides — n'utilise pas pytest pour rester sans dépendance."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd

from src.broker.paper_broker import PaperBroker
from src.strategy.momentum_sentiment import (MomentumSentimentStrategy,
                                              StrategyConfig)
from src.backtest.engine import run as backtest_run


def test_paper_broker_pnl():
    b = PaperBroker(initial_cash=1000.0, fee_bps=0)
    b.market("BTC-USD", "buy", 0.01, 50_000)
    b.market("BTC-USD", "sell", 0.01, 51_000)
    assert abs(b.realized_pnl - 10.0) < 1e-6, b.realized_pnl
    print("OK paper broker pnl")


def test_strategy_signal():
    idx = pd.date_range("2024-01-01", periods=300, freq="h")
    close = pd.Series(np.linspace(100, 200, 300), index=idx)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                       "Volume": 1.0}, index=idx)
    strat = MomentumSentimentStrategy(StrategyConfig())
    sig = strat.evaluate(df, reddit=0.5, futures_ls=0.3, coingecko=0.2, fear_greed=0.4)
    assert sig.decision in {"LONG", "FLAT", "SHORT"}
    assert -1.0 <= sig.score <= 1.0
    print(f"OK strategy signal: score={sig.score:.3f} decision={sig.decision}")


def test_backtest_runs():
    idx = pd.date_range("2024-01-01", periods=500, freq="h")
    rng = np.random.default_rng(42)
    rets = rng.normal(0, 0.01, 500)
    close = pd.Series(100 * (1 + pd.Series(rets)).cumprod().to_numpy(), index=idx)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                       "Volume": 1.0}, index=idx)
    strat = MomentumSentimentStrategy(StrategyConfig())
    res = backtest_run(df, strat)
    assert isinstance(res.total_return, float)
    print(f"OK backtest: trades={res.trades} sharpe={res.sharpe:.2f} "
          f"ret={res.total_return:+.2%}")


if __name__ == "__main__":
    test_paper_broker_pnl()
    test_strategy_signal()
    test_backtest_runs()
    print("\nAll smoke tests passed.")
