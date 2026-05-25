"""Data client for OHLCV data with free fallbacks.

Attempts to fetch data from Gemini public API. If the request fails (e.g., unsupported
date range or symbol), it falls back to CCXT (Binance) then Yahoo Finance (yfinance),
which are completely free and require no API key.

All sources return a ``pandas.DataFrame`` with columns ``Open``, ``High``,
``Low``, ``Close`` and ``Volume`` and a ``DateTimeIndex``.
"""
from __future__ import annotations

import datetime
import logging
import pandas as pd
import requests

# Optional import – will be installed if missing
try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

# Optional import – will be installed if missing
try:
    import ccxt
except Exception:  # pragma: no cover
    ccxt = None

# Mapping from common interval names to Gemini API format
GEMINI_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1hr",
    "1hr": "1hr",
    "6h": "6hr",
    "6hr": "6hr",
    "1d": "1day",
    "1day": "1day",
}

# Mapping from common interval names to CCXT format
CCXT_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1hr": "1h",
    "6h": "6h",
    "6hr": "6h",
    "1d": "1d",
    "1day": "1d",
}

# Headers pour eviter les erreurs 406 sur Gemini
_GEMINI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _fetch_from_gemini(symbol: str, start_ts: int, end_ts: int, interval: str) -> pd.DataFrame:
    """Internal helper to query Gemini API."""
    gemini_symbol = symbol.replace("-", "").lower()
    # Map interval to Gemini's expected format
    gemini_interval = GEMINI_INTERVAL_MAP.get(interval, interval)
    url = f"https://api.gemini.com/v2/candles/{gemini_symbol}/{gemini_interval}"
    params = {"since": start_ts, "until": end_ts, "limit": 1000}
    try:
        resp = requests.get(url, headers=_GEMINI_HEADERS, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(
            data,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        df.columns = df.columns.str.title()
        return df.dropna()
    except Exception as e:
        logging.warning(f"Gemini API error: {e}")
        return pd.DataFrame()


def _fetch_from_ccxt(symbol: str, start: str | None, end: str | None, interval: str = "1d") -> pd.DataFrame:
    """Fallback using ccxt to fetch from Binance (free public API)."""
    if ccxt is None:
        raise RuntimeError("ccxt is not installed.")
    try:
        exchange = ccxt.binance()
        exchange.load_markets()
        # Convert symbol like BTC-USD to BTC/USDT for Binance
        if "-" not in symbol:
            logging.warning(f"Invalid symbol format: {symbol}, expected like BTC-USD")
            return pd.DataFrame()
        base, quote = symbol.split("-", 1)
        if quote.upper() == "USD":
            quote = "USDT"
        ccxt_symbol = f"{base}/{quote}"
        # Map interval to CCXT format
        ccxt_interval = CCXT_INTERVAL_MAP.get(interval, interval)
        # Convert dates to timestamps in milliseconds
        since = None
        if start:
            since = int(pd.Timestamp(start).timestamp() * 1000)
        limit = 1000
        ohlcv = exchange.fetch_ohlcv(ccxt_symbol, ccxt_interval, since=since, limit=limit)
        if not ohlcv:
            return pd.DataFrame()
        df = pd.DataFrame(ohlcv, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
        df["Date"] = pd.to_datetime(df["Date"], unit="ms")
        df.set_index("Date", inplace=True)
        if end:
            end_dt = pd.Timestamp(end)
            df = df[df.index <= end_dt]
        return df.dropna()
    except Exception as e:
        logging.warning(f"CCXT fallback error: {e}")
        return pd.DataFrame()


def _fetch_from_yahoo(symbol: str, start: str | None, end: str | None) -> pd.DataFrame:
    """Fallback using yfinance / Yahoo Finance."""
    if yf is None:
        raise RuntimeError("yfinance is not installed.")
    # Yahoo expects symbols like ``BTC-USD`` (keep dash)
    yahoo_symbol = symbol
    try:
        ticker = yf.Ticker(yahoo_symbol)
        df = ticker.history(start=start, end=end)
        if df.empty:
            return pd.DataFrame()
        # Keep only the needed columns and rename them
        df = df[["Open", "High", "Low", "Close", "Volume"]].copy()
        df.index.name = "Date"
        return df.dropna()
    except Exception as e:
        logging.warning(f"Yahoo fallback error: {e}")
        return pd.DataFrame()


def fetch_ohlcv(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    period: str = "60d",
    interval: str = "1h",
) -> pd.DataFrame:
    """Return OHLCV data for *symbol*.

    The function first tries the Gemini public API. If that fails (e.g. because the
    requested date range is not supported), it falls back to CCXT (Binance) then
    Yahoo Finance (yfinance). The returned DataFrame always has the same column
    names (``Open``, ``High``, ``Low``, ``Close``, ``Volume``) and a
    ``DateTimeIndex``.
    """
    # Convert start / end to timestamps (ms) for Gemini
    now = datetime.datetime.utcnow()
    if start:
        start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
    else:
        # Interpret period like "60d"
        if period.endswith("d"):
            days = int(period[:-1])
            start_dt = now - datetime.timedelta(days=days)
        else:
            start_dt = now - datetime.timedelta(days=60)
    if end:
        end_dt = datetime.datetime.strptime(end, "%Y-%m-%d")
    else:
        end_dt = now

    start_ts = int(start_dt.timestamp() * 1000)
    end_ts = int(end_dt.timestamp() * 1000)

    # Try Gemini first
    df = _fetch_from_gemini(symbol, start_ts, end_ts, interval)
    if not df.empty:
        return df

    # Fallback to CCXT (Binance)
    df = _fetch_from_ccxt(symbol, start, end, interval)
    if not df.empty:
        return df

    # Fallback to Yahoo Finance (daily data only; interval ignored)
    return _fetch_from_yahoo(symbol, start, end)


def latest_price(symbol: str) -> float | None:
    """Get the latest price for *symbol* using the same source hierarchy."""
    df = fetch_ohlcv(symbol, period="2d", interval="1d")
    if df.empty:
        return None
    return float(df["Close"].iloc[-1])