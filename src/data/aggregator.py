"""Agrégateur — fusionne toutes les sources en un MarketSnapshot.

Chaque champ sentiment est float | None ∈ [-1, 1].

v3 — correctifs thread-safety :
  - _cache et _breakers protégés par RLock
  - _fetch() n'imbrique plus d'executor dans un executor :
    les fonctions source sont appelées directement dans le thread soumis
  - ThreadPoolExecutor partagé dimensionné pour N_sources × N_symbols
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import pandas as pd

from . import binance_client, binance_futures_client, fear_greed_client
from .coingecko_client import community_score as cg_community_score
from .coingecko_client import price as cg_price
from .dexscreener_client import community_score as dex_community_score
from .dexscreener_client import price as dex_price
from .ohlcv_client import fetch_ohlcv
from ..agent.openrouter_client import sentiment as or_sentiment
from .reddit_client import RedditClient

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache TTL par source — thread-safe via _cache_lock
# ---------------------------------------------------------------------------

_cache_lock = threading.RLock()
_cache: dict[str, dict] = {}

_SOURCE_TTL: dict[str, float] = {
    "fear_greed":     300.0,
    "coingecko":      300.0,
    "reddit":         120.0,
    "futures_ls":      60.0,
    "binance_change":  30.0,
    "binance_taker":   30.0,
    "ohlcv":          300.0,
    "groq":            60.0,
    "price":           30.0,
}

_SOURCE_TIMEOUT: dict[str, float] = {
    "fear_greed":     5.0,
    "coingecko":     15.0,
    "reddit":        10.0,
    "futures_ls":     5.0,
    "binance_change": 5.0,
    "binance_taker":  5.0,
    "ohlcv":         15.0,
    "groq":          10.0,
}


def _normalize(symbol: str) -> str:
    """Normalise un symbole CryptoCom/USD vers le format avec tiret.

    AAVEUSD → AAVE-USD
    BTCUSD  → BTC-USD
    JITOSOLUSD → JITOSOL-USD
    """
    s = symbol.upper()
    if "-" in s or ":" in s:
        return s.replace(":", "-")
    if s.endswith("USD"):
        base = s[:-3]
        return f"{base}-USD"
    if s.endswith("USD"):
        base = s[:-4]
        return f"{base}-USD"
    if s.endswith("EUR"):
        base = s[:-3]
        return f"{base}-EUR"
    return s


def _cache_key(source: str, symbol: str) -> str:
    return f"{source}:{symbol}"


def _cached(source: str, symbol: str) -> tuple[bool, float | None]:
    key = _cache_key(source, symbol)
    with _cache_lock:
        entry = _cache.get(key)
    if entry and time.time() - entry["ts"] < _SOURCE_TTL.get(source, 60.0):
        return True, entry["value"]
    return False, None


def _store(source: str, symbol: str, value: float | None) -> float | None:
    with _cache_lock:
        _cache[_cache_key(source, symbol)] = {"value": value, "ts": time.time()}
    return value


# ---------------------------------------------------------------------------
# Circuit-breaker — thread-safe via _breaker_lock
# ---------------------------------------------------------------------------

_breaker_lock = threading.Lock()

@dataclass
class _Breaker:
    failures:     int   = 0
    open_until:   float = 0.0
    max_failures: int   = 3
    cooldown:     float = 120.0

    def is_open(self) -> bool:
        return time.time() < self.open_until

    def record_success(self) -> None:
        self.failures   = 0
        self.open_until = 0.0

    def record_failure(self, name: str) -> None:
        self.failures += 1
        if self.failures >= self.max_failures:
            self.open_until = time.time() + self.cooldown


_breakers: dict[str, _Breaker] = {}


def _breaker(source: str) -> _Breaker:
    with _breaker_lock:
        if source not in _breakers:
            _breakers[source] = _Breaker()
        return _breakers[source]


# ---------------------------------------------------------------------------
# Fetch générique — appel DIRECT (pas d'executor imbriqué)
# ---------------------------------------------------------------------------

def _fetch_direct(source: str, symbol: str, fn) -> float | None:
    """Exécute fn() directement dans le thread courant.
    Cache + circuit-breaker, mais pas de soumission à un executor.
    À appeler depuis un thread déjà soumis à l'executor principal.
    """
    hit, cached_val = _cached(source, symbol)
    if hit:
        return cached_val

    br = _breaker(source)
    if br.is_open():
        logger.debug("Circuit-breaker fermé source=%s symbol=%s", source, symbol)
        return None

    try:
        val = fn()
        br.record_success()
        return _store(source, symbol, val)
    except Exception:
        br.record_failure(source)
        return None


# ---------------------------------------------------------------------------
# MarketSnapshot
# ---------------------------------------------------------------------------

@dataclass
class MarketSnapshot:
    symbol: str
    price:  float
    ohlcv:  pd.DataFrame

    groq:             float | None = None
    reddit:           float | None = None
    futures_ls:       float | None = None
    coingecko_social: float | None = None
    fear_greed:       float | None = None
    binance_change:   float | None = None
    binance_taker:    float | None = None

    sources_status: dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# DataAggregator
# ---------------------------------------------------------------------------

# N_SOURCES sources indépendantes × jusqu'à ~8 symboles typiques
# + threads snapshot du trading_agent → 64 workers suffisants sans starve
_EXECUTOR = ThreadPoolExecutor(max_workers=64, thread_name_prefix="agg")


class DataAggregator:

    def __init__(
        self,
        reddit:       RedditClient,
        reddit_subs:  dict[str, float] | list[str],
        reddit_limit: int = 50,
    ):
        self.reddit       = reddit
        self.reddit_subs  = reddit_subs
        self.reddit_limit = reddit_limit

    def snapshot(self, symbol: str, period: str = "60d", interval: str = "1h") -> MarketSnapshot:
        """Collecte toutes les sources en parallèle (sans imbrication d'executors)."""
        norm = _normalize(symbol)  # AAVEUSD → AAVE-USD

        # Chaque source est soumise comme une tâche indépendante dans _EXECUTOR.
        # _fetch_direct() est appelé DANS le thread soumis → pas d'imbrication.
        def _run(source, fn):
            return source, _fetch_direct(source, symbol, fn)

        submitted = {
            _EXECUTOR.submit(_run, "ohlcv",          lambda: fetch_ohlcv(norm, period=period, interval=interval)): "ohlcv",
            _EXECUTOR.submit(_run, "futures_ls",     lambda: binance_futures_client.long_short_ratio(norm)): "futures_ls",
            _EXECUTOR.submit(_run, "coingecko",      lambda: cg_community_score(symbol)):             "coingecko",
            _EXECUTOR.submit(_run, "fear_greed",     lambda: fear_greed_client.normalized_score()):   "fear_greed",
            _EXECUTOR.submit(_run, "binance_change", lambda: binance_client.price_change_score(norm)): "binance_change",
            _EXECUTOR.submit(_run, "binance_taker",  lambda: binance_client.taker_pressure_score(norm)): "binance_taker",
            _EXECUTOR.submit(_run, "reddit",         lambda: self.reddit.sentiment(symbol, self.reddit_subs, self.reddit_limit)): "reddit",
            _EXECUTOR.submit(_run, "dexscreener",   lambda: dex_community_score(symbol)):            "dexscreener",
        }

        results: dict[str, float | None] = {}
        df = pd.DataFrame()

        # Timeout global : après 25s on collecte les résultats disponibles
        # et on ignore ceux qui n'ont pas terminé à temps.
        from concurrent.futures import wait
        done, _ = wait(submitted, timeout=25)
        for fut in done:
            try:
                source, val = fut.result()
            except Exception:
                continue  # ignorer silencieusement les sources en échec
            if source == "ohlcv":
                df = val if isinstance(val, pd.DataFrame) else pd.DataFrame()
            else:
                results[source] = val

        # ── Prix ────────────────────────────────────────────────────────────
        price: float = 0.0
        if not df.empty and "Close" in df.columns:
            price = float(df["Close"].iloc[-1])

        if price <= 0:
            hit, cached_price = _cached("price", symbol)
            if hit and cached_price:
                price = float(cached_price)

        if price <= 0:
            for fallback in (lambda: binance_client.price(norm), lambda: cg_price(symbol)):
                try:
                    val = fallback()
                    if val and float(val) > 0:
                        price = float(val)
                        break
                except Exception:
                    pass

        if price > 0:
            _store("price", symbol, price)
        else:
            price = 1.0

        # ── Sentiment final : fallback Reddit → CoinGecko → DexScreener
        or_score = results.get("reddit") or results.get("coingecko") or dex_community_score(symbol)

        status = {k: v is not None for k, v in results.items()}

        return MarketSnapshot(
            symbol=symbol,
            price=price,
            ohlcv=df,
            groq=or_score,
            reddit=results.get("reddit"),
            futures_ls=results.get("futures_ls"),
            coingecko_social=results.get("coingecko"),
            fear_greed=results.get("fear_greed"),
            binance_change=results.get("binance_change"),
            binance_taker=results.get("binance_taker"),
            sources_status=status,
        )