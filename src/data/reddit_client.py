"""Reddit sentiment sans API officielle — chaîne de fallback RSS → old.reddit → www.reddit.

Stratégie d'accès par ordre de fiabilité :
  1. RSS  : reddit.com/r/{sub}/search.rss  — rarement bloqué, XML léger
  2. old  : old.reddit.com/r/{sub}/search.json — souvent moins filtré
  3. www  : www.reddit.com/r/{sub}/search.json — fallback standard

Chaque niveau est tenté uniquement si le précédent échoue (403/429/503/timeout).
"""
from __future__ import annotations

import json
import math
import random
import re
import time
import logging
import xml.etree.ElementTree as ET
from typing import Callable

import requests

logger = logging.getLogger(__name__)

# ── Rate-limit ────────────────────────────────────────────────────────────────
_last_request: float = 0.0
_MIN_GAP = 3.0  # secondes entre deux requêtes
_JITTER = 2.0  # jitter aléatoire supplémentaire

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: dict[str, tuple[float, float | None]] = {}
_CACHE_TTL = 600  # 10 min (doublé pour 88 actifs)

# Cache des échecs (429) pour éviter de retenter un subreddit/méthode bloqué
_failure_cache: dict[str, tuple[float, int]] = {}
_FAILURE_CACHE_TTL = 600  # 10 min au lieu de 2 (pour 88 actifs)

# Tokens trop obscurs pour Reddit — inutile de chercher
_SMALL_TOKENS = frozenset({
    "",  # fallback
})
# On construit dynamiquement à partir du fait qu'un token < 3 lettres
# ou avec des chiffres n'aura aucun résultat Reddit pertinent

# ── Lexique ───────────────────────────────────────────────────────────────────
_BULL = frozenset({
    "buy", "long", "bull", "bullish", "moon", "pump", "rocket", "calls",
    "rally", "breakout", "ath", "support", "accumulate", "bullrun",
    "mooooon", "gains", "green", "up", "hodl", "btfd",
    "wagmi", "lfg", "bottom", "oversold", "breakthrough",
    "adoption", "momentum", "surge", "flip",
    "supercycle", "institutional", "etf", "approve",
})
_BEAR = frozenset({
    "sell", "short", "bear", "bearish", "dump", "crash", "puts", "rekt",
    "rug", "scam", "resistance", "capitulate", "panic", "overbought",
    "fud", "red", "down", "ceiling",
    "ngmi", "fear", "uncertainty", "worried", "overvalued",
    "bubble", "selloff", "capitulation", "top", "correction",
    "dead", "fomo", "whale", "dumping",
})

# Namespace Atom utilisé par Reddit dans ses flux RSS
_RSS_NS = {"atom": "http://www.w3.org/2005/Atom"}

_HEADERS_RSS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}
_HEADERS_JSON = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}

_BLOCKED = (403, 429, 503)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _score(text: str) -> int:
    tokens = re.findall(r"[a-z]+", text.lower())
    return sum(1 if w in _BULL else -1 if w in _BEAR else 0 for w in tokens)


def _cache_key(ticker: str, subs: dict[str, float]) -> str:
    return json.dumps({"t": ticker, "s": sorted(subs.items())}, separators=(",", ":"))


def _rate_limit() -> None:
    global _last_request
    gap = _MIN_GAP + random.uniform(0, _JITTER)
    wait = gap - (time.time() - _last_request)
    if wait > 0:
        time.sleep(wait)
    _last_request = time.time()


# ── Fetchers ──────────────────────────────────────────────────────────────────

def _is_blocked(method: str, sub: str) -> bool:
    """Vérifie si un subreddit+method a récemment retourné 429."""
    key = f"{method}:{sub}"
    entry = _failure_cache.get(key)
    if entry and time.time() - entry[0] < _FAILURE_CACHE_TTL:
        return True
    return False


def _mark_blocked(method: str, sub: str, status: int = 429) -> None:
    """Enregistre qu'un subreddit+method est bloqué."""
    if status in _BLOCKED:
        key = f"{method}:{sub}"
        _failure_cache[key] = (time.time(), status)


def _fetch_rss(sub: str, query: str, limit: int) -> list[str] | None:
    """Posts via flux RSS — méthode la plus légère et la moins bloquée."""
    if _is_blocked("rss", sub):
        logger.debug("RSS r/%s → skip (429 cache)", sub)
        return None
    url = f"https://www.reddit.com/r/{sub}/search.rss"
    params = {"q": query, "sort": "new", "limit": limit, "restrict_sr": "1"}
    try:
        _rate_limit()
        resp = requests.get(url, headers=_HEADERS_RSS, params=params, timeout=10)
        if resp.status_code in _BLOCKED:
            logger.debug("RSS r/%s → HTTP %d", sub, resp.status_code)
            _mark_blocked("rss", sub, resp.status_code)
            return None
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        # Format Atom (habituel sur Reddit)
        texts = [
            f"{e.findtext('atom:title', '', _RSS_NS)} {e.findtext('atom:summary', '', _RSS_NS)}"
            for e in root.findall("atom:entry", _RSS_NS)
        ]
        # Fallback RSS 2.0
        if not texts:
            texts = [
                f"{i.findtext('title', '')} {i.findtext('description', '')}"
                for i in root.findall(".//item")
            ]
        return texts or None
    except Exception as exc:
        logger.debug("RSS r/%s : %s", sub, exc)
        return None


def _fetch_json(base: str, sub: str, query: str, limit: int) -> list[str] | None:
    """Posts via endpoint JSON (old.reddit ou www.reddit)."""
    method = f"json:{base.split('.')[0]}"
    if _is_blocked(method, sub):
        logger.debug("JSON(%s) r/%s → skip (429 cache)", base, sub)
        return None
    url = f"https://{base}/r/{sub}/search.json"
    params = {"q": query, "sort": "new", "limit": limit, "restrict_sr": "1"}
    try:
        _rate_limit()
        resp = requests.get(url, headers=_HEADERS_JSON, params=params, timeout=10)
        if resp.status_code in _BLOCKED:
            logger.debug("JSON(%s) r/%s → HTTP %d", base, sub, resp.status_code)
            _mark_blocked(method, sub, resp.status_code)
            return None
        resp.raise_for_status()
        children = resp.json().get("data", {}).get("children", [])
        texts = [
            f"{(d := c.get('data') or {}).get('title', '')} {d.get('selftext', '')}"
            for c in children
        ]
        return texts or None
    except Exception as exc:
        logger.debug("JSON(%s) r/%s : %s", base, sub, exc)
        return None


def _should_skip_reddit(query: str) -> bool:
    """Ignore Reddit pour les tokens trop petits/obscurs."""
    if len(query) < 3 or any(c.isdigit() for c in query):
        return True
    return False


def _fetch_posts(sub: str, query: str, limit: int) -> list[str]:
    """Chaîne RSS → old.reddit JSON → www.reddit JSON."""
    if _should_skip_reddit(query):
        return []
    fetchers: list[Callable[[], list[str] | None]] = [
        lambda: _fetch_rss(sub, query, limit),
        lambda: _fetch_json("old.reddit.com", sub, query, limit),
        lambda: _fetch_json("www.reddit.com", sub, query, limit),
    ]
    for fetcher in fetchers:
        result = fetcher()
        if result is not None and len(result) > 0:
            return result
        # Si le premier fallback (RSS) est bloqué, on short-circuite
    return []


# ── Client ────────────────────────────────────────────────────────────────────

class RedditClient:
    """Client Reddit sans credentials — RSS → old.reddit → www.reddit."""

    def __init__(self, client_id: str = "", client_secret: str = "", user_agent: str = ""):
        pass  # Paramètres conservés pour compatibilité ascendante

    def sentiment(
        self,
        ticker: str,
        subs: dict[str, float] | list[str],
        limit: int = 25,
    ) -> float | None:
        """Score normalisé ∈ ]-1, 1[ via tanh, ou None si aucun signal.

        Cache TTL 5 min pour limiter les appels réseau.
        """
        if isinstance(subs, list):
            subs = {s: 1.0 for s in subs}
        if not subs:
            return None

        key = _cache_key(ticker, subs)
        now = time.time()
        cached = _cache.get(key)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]

        query = ticker.split("-")[0].upper()
        weighted_sum = 0.0
        weight_total = 0.0
        n_posts = 0

        for sub, weight in subs.items():
            w = float(weight)
            for text in _fetch_posts(sub, query, limit):
                sc = _score(text)
                if sc == 0:
                    continue
                weighted_sum += w * sc
                weight_total += w
                n_posts += 1

        score: float | None = None
        if n_posts > 0 and weight_total > 0.0:
            score = math.tanh(weighted_sum / weight_total)

        _cache[key] = (now, score)
        logger.debug(
            "Reddit %s: %s (%d posts non-neutres)",
            ticker,
            f"{score:+.3f}" if score is not None else "None",
            n_posts,
        )
        return score