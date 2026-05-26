"""OHLCV data — CryptoCom → CCXT/Binance → Yahoo Finance (tous gratuits, sans clé).

Cascade de fallbacks :
  1. CryptoCom public API  (crypto natif, le plus précis)
  2. Binance klines     (via binance_client, CDN public, toujours dispo)
  3. Yahoo Finance      (yfinance, pour actions + crypto)
"""
from __future__ import annotations

import datetime
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Intervalle maps ───────────────────────────────────────────

_CRYPTOCOM_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1hr", "1hr": "1hr",
    "6h": "6hr", "6hr": "6hr",
    "1d": "1day", "1day": "1day",
}

_CCXT_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "1hr": "1h",
    "6h": "6h", "6hr": "6h",
    "1d": "1d", "1day": "1d",
}

_BINANCE_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "1hr": "1h",
    "4h": "4h",
    "6h": "6h", "6hr": "6h",
    "1d": "1d", "1day": "1d",
}

_PERIOD_TO_LIMIT = {
    "1d": 24, "5d": 120, "7d": 168,
    "14d": 336, "30d": 720, "60d": 1440, "90d": 2160,
}


# ── Fetchers individuels ──────────────────────────────────────

def _from_cryptocom(symbol: str, start_ts: int, end_ts: int, interval: str) -> pd.DataFrame:
    """CryptoCom public REST API."""
    from ._http import get_json
    cryptocom_sym      = symbol.replace("-", "").lower()
    cryptocom_interval = _CRYPTOCOM_INTERVAL.get(interval, "1hr")
    url             = f"https://api.cryptocom.com/v2/candles/{cryptocom_sym}/{cryptocom_interval}"
    try:
        data = get_json(url, params={"since": start_ts, "until": end_ts, "limit": 1000})
        if not data or not isinstance(data, list):
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        df.columns = df.columns.str.title()
        return df.dropna()
    except Exception as e:
        logger.debug("CryptoCom OHLCV error %s: %s", symbol, e)
        return pd.DataFrame()


def _from_binance(symbol: str, interval: str, period: str) -> pd.DataFrame:
    """Binance klines via binance_client (CDN, toujours dispo)."""
    from . import binance_client
    limit       = _PERIOD_TO_LIMIT.get(period, 168)
    bn_interval = _BINANCE_INTERVAL.get(interval, "1h")
    df          = binance_client.klines(symbol, bn_interval, limit)
    return df if df is not None else pd.DataFrame()


def _from_ccxt(symbol: str, start: str | None, end: str | None, interval: str) -> pd.DataFrame:
    """Binance via CCXT."""
    try:
        import ccxt
    except ImportError:
        return pd.DataFrame()

    try:
        exchange    = ccxt.binance()
        base, quote = symbol.split("-", 1) if "-" in symbol else (symbol, "USD")
        if quote.upper() == "USD":
            quote = "USDT"
        ccxt_sym      = f"{base}/{quote}"
        ccxt_interval = _CCXT_INTERVAL.get(interval, "1h")
        since         = int(pd.Timestamp(start).timestamp() * 1000) if start else None
        ohlcv         = exchange.fetch_ohlcv(ccxt_sym, ccxt_interval, since=since, limit=1000)
        if not ohlcv:
            return pd.DataFrame()
        df = pd.DataFrame(ohlcv, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        df["Date"] = pd.to_datetime(df["Date"], unit="ms", utc=True)
        df.set_index("Date", inplace=True)
        if end:
            df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df.dropna()
    except Exception as e:
        logger.debug("CCXT error %s: %s", symbol, e)
        return pd.DataFrame()


def _from_yfinance(symbol: str, period: str, interval: str) -> pd.DataFrame:
    """Yahoo Finance fallback."""
    try:
        import yfinance as yf
        # Dégradation de période si la combinaison period/interval n'est pas supportée
        fallback_periods = [period, "30d", "7d", "5d"]
        for p in dict.fromkeys(fallback_periods):  # déduplique en préservant l'ordre
            try:
                df = yf.Ticker(symbol).history(period=p, interval=interval)
                if not df.empty and len(df) >= 5:
                    df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
                    df.index.name = "Date"
                    if p != period:
                        logger.warning("yfinance %s: dégradé %s → %s", symbol, period, p)
                    return df.dropna()
            except Exception:
                continue
        return pd.DataFrame()
    except ImportError:
        return pd.DataFrame()


# ── API publique ──────────────────────────────────────────────

def fetch_ohlcv(
    symbol:   str,
    start:    str | None = None,
    end:      str | None = None,
    period:   str = "60d",
    interval: str = "1h",
) -> pd.DataFrame:
    """Retourne OHLCV pour *symbol* avec cascade de fallbacks.

    Colonnes : Open, High, Low, Close, Volume  +  DateTimeIndex.
    Retourne DataFrame vide si toutes les sources échouent.
    """
    now      = datetime.datetime.utcnow()
    days     = int(period[:-1]) if period.endswith("d") else 60
    start_dt = datetime.datetime.strptime(start, "%Y-%m-%d") if start else now - datetime.timedelta(days=days)
    end_dt   = datetime.datetime.strptime(end,   "%Y-%m-%d") if end   else now

    start_ts = int(start_dt.timestamp() * 1000)
    end_ts   = int(end_dt.timestamp()   * 1000)

    # 1. CryptoCom
    df = _from_cryptocom(symbol, start_ts, end_ts, interval)
    if not df.empty:
        logger.debug("OHLCV via CryptoCom pour %s: %d bars", symbol, len(df))
        return df

    # 2. Binance klines (le plus fiable pour les cryptos)
    df = _from_binance(symbol, interval, period)
    if not df.empty:
        logger.debug("OHLCV via Binance pour %s: %d bars", symbol, len(df))
        return df

    # 3. CCXT
    df = _from_ccxt(symbol, start, end, interval)
    if not df.empty:
        logger.debug("OHLCV via CCXT pour %s: %d bars", symbol, len(df))
        return df

    # 4. yfinance
    df = _from_yfinance(symbol, period, interval)
    if not df.empty:
        logger.debug("OHLCV via yfinance pour %s: %d bars", symbol, len(df))
        return df

    logger.error("OHLCV introuvable pour %s (toutes les sources ont échoué)", symbol)
    return pd.DataFrame()


def latest_price(symbol: str) -> float | None:
    """Prix courant via la même cascade."""
    df = fetch_ohlcv(symbol, period="2d", interval="1d")
    if df.empty:
        return None
    return float(df["Close"].iloc[-1])