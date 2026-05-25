"""Client Groq (Llama 3) — analyse de sentiment marché.

Source principale de sentiment. Fallback sur les autres sources en cas d'échec.
Cache de 5 minutes pour respecter les limites de l'API gratuite.

Utilise l'API Groq avec le modèle Llama 3 70B ou Mixtral.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Cache : {symbol: (timestamp, score)}
_cache: dict[str, tuple[float, Optional[float]]] = {}
CACHE_TTL = 300  # 5 minutes

_client_configured = False
_api_key = None
_base_url = "https://api.groq.com/openai/v1"


def configure(api_key: str) -> None:
    """Configure le client Groq. Appelée au démarrage."""
    global _api_key, _client_configured
    _api_key = api_key
    if not api_key:
        logger.warning("GROQ_API_KEY non définie — Groq désactivé")
        _client_configured = False
        return

    # Test simple de connexion
    try:
        response = requests.get(
            f"{_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5,
        )
        if response.status_code == 200:
            _client_configured = True
            logger.info("Groq configuré avec succès")
        else:
            logger.warning(f"Groq configuration échouée: {response.status_code}")
            _client_configured = False
    except Exception as e:
        logger.warning(f"Impossible de configurer Groq: {e}")
        _client_configured = False


def sentiment(
    symbol: str,
    price: float | None = None,
    change_pct: float | None = None,
    reddit_score: float | None = None,
    futures_ls: float | None = None,
    fear_greed: float | None = None,
) -> Optional[float]:
    """Analyse le sentiment via Groq, avec cache.
    
    Retourne un score normalisé [-1, +1] ou None si indisponible/quota dépassé.
    """
    # Vérifier le cache
    now = time.time()
    if symbol in _cache:
        ts, score = _cache[symbol]
        if now - ts < CACHE_TTL:
            return score

    if not _client_configured or not _api_key:
        return None

    prompt = _build_prompt(symbol, price, change_pct, reddit_score, futures_ls, fear_greed)

    try:
        response = requests.post(
            f"{_base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.1-8b-instant",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 5,
                "stop": ["\n", " "],
            },
            timeout=10,
        )
        if response.status_code != 200:
            _cache[symbol] = (now, None)
            return None
        result = response.json()
        content = result["choices"][0]["message"]["content"]
        score = _parse_score(content) if content else None

        # Mettre en cache (même None pour éviter de re-tenter trop vite)
        _cache[symbol] = (now, score)

        if score is not None:
            logger.info("Groq %s: %+.3f", symbol, score)

        return score

    except Exception:
        # Cache court pour ne pas re-tenter immédiatement en cas d'erreur
        _cache[symbol] = (now, None)
        return None


def _build_prompt(
    symbol: str,
    price: float | None,
    change_pct: float | None,
    reddit_score: float | None,
    futures_ls: float | None,
    fear_greed: float | None,
) -> str:
    """Construit le prompt avec les données disponibles."""
    parts = [f"Analyze the market sentiment for {symbol} today."]

    if price is not None:
        parts.append(f"Price: ${price:.2f}")
    if change_pct is not None:
        direction = "up" if change_pct >= 0 else "down"
        parts.append(f"24h change: {abs(change_pct):+.3f}% ({direction})")
    if reddit_score is not None:
        parts.append(f"Reddit sentiment: {reddit_score:+.3f} (between -1 and +1)")
    if futures_ls is not None:
        parts.append(f"Futures long/short ratio: {futures_ls:+.3f}")
    if fear_greed is not None:
        fear_val = (fear_greed + 1) * 50
        parts.append(f"Fear & Greed Index: {fear_val:.0f}/100")

    parts.append(
        "\nYou must respond with exactly one number, no words, no explanation."
        "\nThe number must be between -1.0 and +1.0."
        "\nExample response: 0.35"
    )

    return "\n".join(parts)


def _parse_score(text: str) -> Optional[float]:
    """Extrait un score flottant depuis la réponse Groq."""
    if not text:
        return None

    # Cherche un nombre à virgule/flottant dans le texte
    match = re.search(r"[-+]?\d+\.?\d*", text.strip())
    if match:
        try:
            score = float(match.group())
            return max(-1.0, min(1.0, score))  # clamp [-1, +1]
        except (ValueError, TypeError):
            pass

    return None