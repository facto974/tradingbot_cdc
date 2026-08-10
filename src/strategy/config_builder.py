"""Construction centralisée de StrategyConfig depuis settings.raw.

But: éviter que run_backtest.py et trading_agent.py divergent silencieusement
sur les paramètres de stratégie (c'était le cas avant: run_backtest.py ne
lisait que 8 champs sur 16, retombant sur les défauts du dataclass pour le
reste — notamment close_threshold, sma_fast/slow, min_momentum_abs).

Utilisation:
    from src.strategy.config_builder import build_strategy_config
    sc = build_strategy_config(settings.raw)
"""
from __future__ import annotations

from .momentum_sentiment import StrategyConfig


def build_strategy_config(raw: dict) -> StrategyConfig:
    strat = raw.get("strategy", {})
    weights = strat.get("weights", {})
    mom_cfg = strat.get("momentum", {})
    thresh = strat.get("thresholds", {})
    sent_cfg = strat.get("sentiment", {})
    risk = raw.get("risk", {})

    return StrategyConfig(
        w_momentum=weights.get("momentum", 0.55),
        w_sentiment=weights.get("sentiment", 0.20),
        w_fear_greed=weights.get("fear_greed", 0.25),
        lookback=mom_cfg.get("lookback_days", 14),
        ema_smooth=mom_cfg.get("ema_smooth", 7),
        threshold_long=thresh.get("long", 0.15),
        threshold_short=thresh.get("short", -0.35),
        close_threshold=thresh.get("close_threshold", 0.00),
        close_short_threshold=thresh.get("close_short_threshold", -0.10),
        allow_short=risk.get("allow_short", False),
        high_conviction=sent_cfg.get("high_conviction", False),
        min_active_sentiment_sources=sent_cfg.get("min_active_sources", 1),
        require_aligned=sent_cfg.get("require_aligned", True),
        min_momentum_abs=sent_cfg.get("min_momentum_abs", 0.05),
        enable_trend_filter=mom_cfg.get("enable_trend_filter", True),
        sma_fast=mom_cfg.get("sma_fast", 24),
        sma_slow=mom_cfg.get("sma_slow", 96),
    )