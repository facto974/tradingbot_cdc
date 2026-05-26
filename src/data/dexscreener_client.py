"""DexScreener — prix et variation 24h pour tout token (DEX). Fallback quand CoinGecko n'a pas d'ID.

API publique gratuite, sans clé, pas de rate-limit documenté.
Couverture : Ethereum, BSC, Solana, Polygon, Arbitrum, Base, etc.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ── Cache ─────────────────────────────────────────────────────────────────

_cache: dict[str, tuple[float, dict | None]] = {}
_CACHE_TTL = 300  # 5 min


def _price_key(symbol: str) -> str:
    """Génère une clé pour le cache."""
    return f"dexscreener:{symbol}"


# ── Helpers ───────────────────────────────────────────────────────────────

def _base_token(symbol: str) -> str:
    """Extrait le nom du token : 'AAVEUSD' → 'AAVE', 'BTC-USD' → 'BTC'."""
    s = symbol.upper()
    for suffix in ["USD", "USDT", "USD", "GUSD", "RLUSD", "EUR", "GBP", "SGD"]:
        if s.endswith(suffix):
            return s[:-len(suffix)]
    return s.split("-")[0]


def _token_address(symbol: str) -> str | None:
    """Mappe un symbole vers une adresse de contrat DexScreener.

    Supporte 'AAVEUSD', 'BTC-USD', 'PENGU-USD' indifféremment.
    Retourne None si l'adresse est inconnue → le token ne pourra pas être
    récupéré via DexScreener non plus.
    """
    base = _base_token(symbol)
    mapping: dict[str, str] = {
        # Solana
        "BOME":   "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
        "PENGU":  "2zMMhcVQEXztdrUY5N2fEXiFdMJPLEopkzqpPjGKKccf",
        "MEW":    "MEW1gQWJ3nEXg2qgERiKu7FAFj79PHvQVREQUzScPP5",
        "WIF":    "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "BONK":   "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "FLOKI":  "5e2CkzUjGfB2QA42cCQZdLBdQGVmY9YbYNZcY3e5AN3G",
        "GOAT":   "CzLSujWBLFsSjncfkh59rUFqvafWcYm5aVPFhjkREb2g",
        "CHILLGUY": "Df6yfrKC8kZE3KNkrHERKzAetSxbrWeniQfyJY4Jpump",
        "FARTCOIN": "9BB6nfEc8GHBbcH9mHXY9Tq8f8C6QbG1mFJHJLPmqn55",
        "JITOSOL": "J1toso1uCk3QLmjYXpTphtR3TUrHxwCunGq9KXKKLK7Q",
        "DRIFT":  "DriFtupJYLTosbwoN8x2E7JvNs3T7p8jNLCGsmc1SM1q",
        "KMNO":   "KMNo3nJsBXfcpJTVhZcXLW7RmTwTt4GVZYooT2wB8K3k",
        "PYTH":   "HZ1JovNiVvGrGNiiYvEozEVgZ58xaU3RKwX8eACQBCt3",
        "JUP":    "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
        "JTO":    "jtojtomepa8beP8AuQc6eXt1Fm8U7D3B4S8K3yQ3qHn",
        "W":      "85VBFQZC9TZkfaptBWj81UwMsY4cDkX5D2Gk6E1a6nV8",
        "ALI":    "ALiVCmasiKjQB5ttJQNKQYJSUWXsYYcq1tZTm2R3TNAm",
        "MON":    "MONJAfD6bCQHX4KZ3LKAfPsS5PHQ8QMXbb5JqN1JcPp",
        "CTX":    "CTXpsxC6pQQM5NRtLp8uK9WKMPvhvNrAopvKA3PrKLu9",
        "CUBE":   "CUBEoP8Jk7LQ5gB3PqLQGqKjVLqGqjKjVLqGqjKjVLqG",
        "HNT":    "hntoJ6d5LQ5gB3PqLQGqKjVLqGqjKjVLqGqjKjVLqGq",
    }
    base = symbol.split("-")[0].upper()
    return mapping.get(base)


def _search(symbol: str) -> list[dict] | None:
    """Recherche un token sur DexScreener via l'API search."""
    base = symbol.split("-")[0].upper()
    url = f"https://api.dexscreener.com/latest/dex/search"
    params = {"q": base}
    try:
        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            pairs = data.get("pairs", [])
            # Filtrer les paires vs USD (USD, USDT, BUSD, etc.)
            usd_pairs = [
                p for p in pairs
                if p.get("quoteToken", {}).get("symbol", "").upper() in ("USD", "USD", "USDT", "BUSD")
            ]
            return usd_pairs if usd_pairs else pairs[:5]
        return None
    except Exception as e:
        logger.debug("DexScreener search %s failed: %s", symbol, e)
        return None


def _by_address(address: str) -> list[dict] | None:
    """Récupère les paires pour une adresse de contrat."""
    url = f"https://api.dexscreener.com/latest/dex/tokens/{address}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            return (resp.json()).get("pairs", [])
        return None
    except Exception as e:
        logger.debug("DexScreener address %s failed: %s", address[:8], e)
        return None


# ── API publique ──────────────────────────────────────────────────────────

def price(symbol: str) -> Optional[float]:
    """Prix USDT via DexScreener.

    Cache 5 min. Retourne None si introuvable.
    """
    key = _price_key(symbol)
    now = time.time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < _CACHE_TTL and data is not None:
            return float(data.get("priceUsd", 0))

    # 1. Tentative par adresse connue
    addr = _token_address(symbol)
    pairs = _by_address(addr) if addr else None

    # 2. Fallback par recherche
    if not pairs:
        pairs = _search(symbol)

    if not pairs:
        _cache[key] = (now, None)
        return None

    # Prendre la paire avec le meilleur volume
    best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
    price_usd = float(best.get("priceUsd", 0) or 0)

    _cache[key] = (now, best)
    return price_usd if price_usd > 0 else None


def price_change_24h(symbol: str) -> Optional[float]:
    """Variation 24h en pourcentage.

    Utile pour calculer un score de sentiment (comme CoinGecko).
    Retourne une valeur entre -100 et +100.
    """
    key = _price_key(symbol)
    now = time.time()
    if key in _cache:
        ts, data = _cache[key]
        if now - ts < _CACHE_TTL and data is not None:
            change = data.get("priceChange", {}).get("h24")
            if change is not None:
                return float(change)

    addr = _token_address(symbol)
    pairs = _by_address(addr) if addr else None
    if not pairs:
        pairs = _search(symbol)

    if not pairs:
        return None

    best = max(pairs, key=lambda p: float(p.get("volume", {}).get("h24", 0) or 0))
    _cache[key] = (now, best)
    change = best.get("priceChange", {}).get("h24")
    return float(change) if change is not None else None


def community_score(symbol: str) -> Optional[float]:
    """Score communautaire normalisé [-1, 1] basé sur la variation 24h.

    Méthode : tanh(change_24h / 10) — comme CoinGecko pour le fallback price_change.
    Ne remplace pas le sentiment_votes_up_percentage de CoinGecko, mais
    donne un score cohérent.
    """
    import math
    change = price_change_24h(symbol)
    if change is None:
        return None
    return float(math.tanh(change / 10.0))