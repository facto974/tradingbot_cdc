"""CoinGecko — prix crypto + score communautaire (free tier, sans clé).

Tous les scores retournent Optional[float] ∈ [-1, 1] ou None si indisponible.
"""
from __future__ import annotations

import logging
import math
import random
import threading
import time
from typing import Optional

from ._http import get_json, to_coingecko_id
from .dexscreener_client import community_score as dex_community_score
from .dexscreener_client import price as dex_price

logger = logging.getLogger(__name__)

BASE = "https://api.coingecko.com/api/v3"

# ---------------------------------------------------------------------------
# Rate limiter token-bucket (thread-safe, sleep HORS du lock)
# ---------------------------------------------------------------------------
_RATE_LIMIT_RPS = 8 / 60.0  # 8 req/min — marge sur le free tier (~10/min)


class _TokenBucket:
    def __init__(self, rate: float, burst: int = 3) -> None:
        self._rate   = rate
        self._burst  = float(burst)
        self._tokens = float(burst)
        self._last   = time.monotonic()
        self._lock   = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now          = time.monotonic()
                self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
                self._last   = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            time.sleep(wait)   # hors du lock


_rate_limiter = _TokenBucket(rate=_RATE_LIMIT_RPS, burst=3)

# ---------------------------------------------------------------------------
# Déduplication des requêtes en vol
# ---------------------------------------------------------------------------
_inflight_lock = threading.Lock()
_inflight: dict[str, threading.Event] = {}

# ---------------------------------------------------------------------------
# Caches locaux
# ---------------------------------------------------------------------------
_cache_lock    = threading.Lock()
_price_cache:    dict[str, dict] = {}
_overview_cache: dict[str, dict] = {}
_batch_cache:    dict             = {"ts": 0.0, "data": {}}
_batch_lock      = threading.Lock()

_PRICE_TTL    = 300
_OVERVIEW_TTL = 1800
_BATCH_TTL    = 600

_MAX_RETRIES  = 4
_BASE_BACKOFF = 10.0


def _cached(cache: dict, key: str, ttl: int):
    with _cache_lock:
        entry = cache.get(key)
    if entry and time.time() - entry["ts"] < ttl:
        return entry["value"]
    return None


def _store(cache: dict, key: str, value):
    with _cache_lock:
        cache[key] = {"value": value, "ts": time.time()}
    return value


# ---------------------------------------------------------------------------
# HTTP avec rate-limit + déduplication + backoff exponentiel
# ---------------------------------------------------------------------------

def _get_with_429(url: str, params: dict | None = None) -> dict | None:
    flight_key = url + str(sorted((params or {}).items()))

    with _inflight_lock:
        if flight_key in _inflight:
            event   = _inflight[flight_key]
            waiting = True
        else:
            event   = threading.Event()
            _inflight[flight_key] = event
            waiting = False

    if waiting:
        event.wait(timeout=180)
        # Le thread pilote a mis le résultat en cache — l'appelant le trouvera
        # via _cached() dans price() / coin_overview() après ce retour.
        return _SENTINEL   # valeur spéciale : "va relire le cache"

    backoff = _BASE_BACKOFF
    try:
        for attempt in range(_MAX_RETRIES):
            _rate_limiter.acquire()
            try:
                return get_json(url, params=params)
            except RuntimeError as exc:
                msg = str(exc)
                if "429" not in msg:
                    logger.debug("CoinGecko error (non-429): %s", msg)
                    return None
                retry_after: float | None = None
                for part in msg.split():
                    if part.isdigit():
                        candidate = float(part)
                        if candidate > 1:
                            retry_after = candidate
                            break
                sleep_time = retry_after if retry_after else backoff + random.uniform(0, backoff * 0.2)
                time.sleep(sleep_time)
                backoff = min(backoff * 2, 120)
            except Exception as exc:
                logger.debug("CoinGecko unexpected error: %s", exc)
                return None
        return None
    finally:
        with _inflight_lock:
            _inflight.pop(flight_key, None)
        event.set()


# Sentinel renvoyé aux threads waiters pour distinguer "va relire le cache"
# de "la requête a échoué" (None).
class _SentinelType:
    pass
_SENTINEL = _SentinelType()


# ---------------------------------------------------------------------------
# Batch price
# ---------------------------------------------------------------------------

def _batch_price(ids: set[str]) -> dict[str, float]:
    # Lecture rapide hors lock
    if time.time() - _batch_cache["ts"] < _BATCH_TTL:
        return _batch_cache["data"]

    with _batch_lock:
        # Double-check sous lock
        if time.time() - _batch_cache["ts"] < _BATCH_TTL:
            return _batch_cache["data"]

        ids_list = list(ids)
        result: dict[str, float] = {}

        for i in range(0, len(ids_list), 50):
            batch = ids_list[i: i + 50]
            data  = _get_with_429(
                f"{BASE}/simple/price",
                {"ids": ",".join(batch), "vs_currencies": "usd"},
            )
            if data and not isinstance(data, _SentinelType):
                for cid, v in data.items():
                    if isinstance(v, dict) and "usd" in v:
                        result[cid] = float(v["usd"])

        with _cache_lock:
            _batch_cache["ts"]   = time.time()
            _batch_cache["data"] = result
            for cid, val in result.items():
                _price_cache[cid] = {"value": val, "ts": time.time()}

        return result


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------

def price(symbol: str, vs: str = "usd") -> Optional[float]:
    cid = to_coingecko_id(symbol)

    cached = _cached(_price_cache, cid, _PRICE_TTL)
    if cached is not None:
        return cached

    # CoinGecko
    batch = _batch_price({cid})
    price_cg = batch.get(cid)
    if price_cg is not None:
        return price_cg

    # Fallback DexScreener
    return dex_price(symbol)


def coin_overview(symbol: str) -> dict | None:
    cid = to_coingecko_id(symbol)

    cached = _cached(_overview_cache, cid, _OVERVIEW_TTL)
    if cached is not None:
        return cached

    data = _get_with_429(
        f"{BASE}/coins/{cid}",
        {
            "localization":   "false",
            "tickers":        "false",
            "market_data":    "true",
            "community_data": "true",
            "developer_data": "false",
            "sparkline":      "false",
        },
    )

    # Waiter : relire le cache (le thread pilote vient de le remplir)
    if isinstance(data, _SentinelType):
        return _cached(_overview_cache, cid, _OVERVIEW_TTL)

    if not data:
        return None

    return _store(_overview_cache, cid, data)


def community_score(symbol: str) -> Optional[float]:
    cid = to_coingecko_id(symbol)
    ov = coin_overview(symbol)

    # Fallback DexScreener pour les tokens sans ID CoinGecko valide
    if not ov or not cid or cid == symbol.lower():
        dex_score = dex_community_score(symbol)
        if dex_score is not None:
            return dex_score

    if not ov:
        return None

    up = ov.get("sentiment_votes_up_percentage")
    if up is not None:
        try:
            return max(-1.0, min(1.0, (float(up) - 50.0) / 50.0))
        except (TypeError, ValueError):
            pass

    md = ov.get("market_data") or {}

    pct_24h = md.get("price_change_percentage_24h")
    if pct_24h is not None:
        try:
            return float(math.tanh(float(pct_24h) / 5.0))
        except (TypeError, ValueError):
            pass

    pct_7d = (md.get("price_change_percentage_7d_in_currency") or {}).get("usd")
    if pct_7d is None:
        pct_7d = md.get("price_change_percentage_7d")
    if pct_7d is not None:
        try:
            return float(math.tanh(float(pct_7d) / 10.0))
        except (TypeError, ValueError):
            pass

    return None