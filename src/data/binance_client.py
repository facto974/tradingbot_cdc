"""Binance public API — OHLCV + sentiment marché (sans clé)."""
from __future__ import annotations

import logging
import math
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

from ._http import get_json, to_binance_symbol

logger = logging.getLogger(__name__)

BASE   = "https://api.binance.com"
MIRROR = "https://data-api.binance.vision"


def _get(endpoint: str, params: dict) -> dict | list | None:
    """GET avec fallback sur le miroir."""
    try:
        return get_json(f"{BASE}{endpoint}", params=params)
    except Exception:
        pass
    try:
        return get_json(f"{MIRROR}{endpoint}", params=params)
    except Exception:
        return None


def ticker_24h(symbol: str) -> dict | None:
    """Ticker 24h complet (prix, variation, volume)."""
    return _get("/api/v3/ticker/24hr", {"symbol": to_binance_symbol(symbol)})


def price(symbol: str) -> Optional[float]:
    """Prix spot actuel."""
    data = _get("/api/v3/ticker/price", {"symbol": to_binance_symbol(symbol)})
    if not data:
        return None
    try:
        return float(data["price"])
    except (KeyError, TypeError, ValueError):
        return None


def price_change_score(symbol: str) -> Optional[float]:
    """Variation 24h normalisée via tanh(pct / 5) → [-1, 1].

    +5%  → ≈ +0.76  |  -5%  → ≈ -0.76  |  +10% → ≈ +0.96
    """
    t = ticker_24h(symbol)
    if not t:
        return None
    try:
        pct = float(t["priceChangePercent"])
    except (KeyError, TypeError, ValueError):
        return None
    return float(math.tanh(pct / 5.0))


def klines(
    symbol: str,
    interval: str = "1h",
    limit: int = 168,
) -> Optional["pd.DataFrame"]:
    """OHLCV depuis Binance Klines — fallback fiable quand yfinance échoue.

    Args:
        symbol:   Symbole (ex: "BTC-USD").
        interval: Intervalle Binance ("1m","5m","15m","30m","1h","4h","1d").
        limit:    Nombre de bougies (max 1000). 168 = 7 jours en 1h.

    Returns:
        DataFrame[Open, High, Low, Close, Volume] avec DateTimeIndex, ou None.
    """
    import pandas as pd

    data = _get("/api/v3/klines", {
        "symbol":   to_binance_symbol(symbol),
        "interval": interval,
        "limit":    limit,
    })
    if not data or not isinstance(data, list):
        return None

    try:
        df = pd.DataFrame(data, columns=[
            "timestamp", "Open", "High", "Low", "Close", "Volume",
            "close_time", "quote_volume", "count",
            "taker_buy_vol", "taker_buy_quote", "ignore",
        ])
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.index.name = "Date"
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception as e:
        logger.warning("Binance klines parse error %s: %s", symbol, e)
        return None


def taker_pressure_score(
    symbol: str,
    interval: str = "1h",
    limit: int = 6,
) -> Optional[float]:
    """Pression acheteur/vendeur via klines → (buy - sell) / total ∈ [-1, 1].

    Index kline : [5]=volume base, [9]=takerBuyBaseVolume.
    """
    data = _get("/api/v3/klines", {
        "symbol":   to_binance_symbol(symbol),
        "interval": interval,
        "limit":    limit,
    })
    if not data or not isinstance(data, list):
        return None

    total_vol = taker_buy_vol = 0.0
    for c in data:
        try:
            total_vol     += float(c[5])
            taker_buy_vol += float(c[9])
        except (TypeError, ValueError, IndexError):
            continue

    if total_vol <= 0:
        return None

    taker_sell_vol = total_vol - taker_buy_vol
    return max(-1.0, min(1.0, (taker_buy_vol - taker_sell_vol) / total_vol))