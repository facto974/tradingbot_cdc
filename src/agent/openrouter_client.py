"""Client OpenRouter — valide/conteste un signal de trading via LLM.
 + fonction sentiment() qui délègue à client_groq si disponible.

Hiérarchie des providers :
  sentiment()  → client_groq (primary) → OpenRouter fallback
  validate()   → Groq direct (primary) → OpenRouter fallback
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from openai import OpenAI, APIError, APITimeoutError

logger = logging.getLogger(__name__)

# ── Modèles ───────────────────────────────────────────────────────────────────

GROQ_BASE_URL  = "https://api.groq.com/openai/v1"
GROQ_MODEL     = "llama-3.3-70b-versatile"   # 30 RPM / 14 400 RPD, sans crédits

OR_BASE_URL    = "https://openrouter.ai/api/v1"
OR_MODELS      = [
    "deepseek/deepseek-v4-flash:free",
    "meta-llama/llama-3.3-70b-instruct:free",
]

# ── API keys ──────────────────────────────────────────────────────────────────

_groq_key: str = ""
_or_key: str = ""


def configure(api_key: str = "", openrouter_api_key: str = "") -> None:
    """Configure les clés API.

    Rétrocompat : configure(api_key="sk-or-...")  → OpenRouter seulement
    Recommandé  : configure(api_key="gsk_...", openrouter_api_key="sk-or-...")
    """
    global _groq_key, _or_key

    # Détection automatique : clé Groq commence par "gsk_"
    if api_key.startswith("gsk_"):
        _groq_key = api_key
    elif api_key:
        _or_key = api_key

    if openrouter_api_key:
        _or_key = openrouter_api_key


# ── Throttle par endpoint ─────────────────────────────────────────────────────

_last_call: dict[str, float] = {}
_DELAYS = {
    GROQ_BASE_URL: 2.1,   # 28 req/min  (limite Groq : 30)
    OR_BASE_URL:   3.5,   # 17 req/min  (limite OR globale : 20)
}


def _throttle(base_url: str) -> None:
    elapsed = time.time() - _last_call.get(base_url, 0.0)
    wait = _DELAYS.get(base_url, 2.0) - elapsed
    if wait > 0:
        time.sleep(wait)
    _last_call[base_url] = time.time()


def _is_rate_limit(exc: Exception) -> bool:
    return isinstance(exc, APIError) and getattr(exc, "status_code", None) == 429


# ── Appel LLM générique ───────────────────────────────────────────────────────

def _call(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict],
    max_tokens: int = 10,
    temperature: float = 0.0,
    timeout: int = 15,
) -> Optional[str]:
    """Appel LLM avec throttle + retry timeout. Retourne le texte ou None."""
    if not api_key:
        return None

    client = OpenAI(base_url=base_url, api_key=api_key)
    _throttle(base_url)

    def _do_call(t: int) -> str:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=t,
        )
        return (resp.choices[0].message.content or "").strip()

    try:
        return _do_call(timeout)
    except APITimeoutError:
        logger.debug("[%s/%s] timeout, retry…", base_url, model)
        time.sleep(2)
        _throttle(base_url)
        try:
            return _do_call(timeout + 5)
        except Exception as exc2:
            logger.warning("[%s/%s] retry échoué : %s", base_url, model, exc2)
            return None
    except APIError as exc:
        if _is_rate_limit(exc):
            logger.warning("[%s/%s] 429 — passage au fallback", base_url, model)
        else:
            logger.warning("[%s/%s] APIError : %s", base_url, model, exc)
        return None
    except Exception as exc:
        logger.warning("[%s/%s] erreur : %s", base_url, model, exc)
        return None


# ── Sentiment ─────────────────────────────────────────────────────────────────

_SENTIMENT_CACHE: dict[str, tuple[float, Optional[float]]] = {}
_SENTIMENT_TTL = 600  # 10 min

_SENTIMENT_SYSTEM = (
    "You are a crypto market analyst. Given data for {symbol}, return ONLY a single "
    "float between -1.0 and +1.0 representing your sentiment (bearish → bullish). "
    "No explanation. Example: 0.35"
)


def sentiment(
    symbol: str,
    price: float | None = None,
    change_pct: float | None = None,
    reddit_score: float | None = None,
    futures_ls: float | None = None,
    fear_greed: float | None = None,
) -> Optional[float]:
    """Analyse le sentiment.

    Tente d'abord client_groq (s'il est importé et configuré), puis
    Groq direct via OpenAI SDK, puis OpenRouter en dernier recours.
    Cache 10 min par symbole.
    """
    now = time.time()
    if symbol in _SENTIMENT_CACHE:
        ts, score = _SENTIMENT_CACHE[symbol]
        if now - ts < _SENTIMENT_TTL:
            return score

    # ── 1. Délégation à client_groq s'il est disponible ──────────────────────
    try:
        import client_groq  # type: ignore
        if getattr(client_groq, "_client_configured", False):
            score = client_groq.sentiment(
                symbol,
                price=price,
                change_pct=change_pct,
                reddit_score=reddit_score,
                futures_ls=futures_ls,
                fear_greed=fear_greed,
            )
            if score is not None:
                _SENTIMENT_CACHE[symbol] = (now, score)
                logger.debug("sentiment %s via client_groq → %.3f", symbol, score)
                return score
    except ImportError:
        pass  # client_groq absent → on continue avec les fallbacks

    # ── 2. Groq direct (OpenAI SDK) ───────────────────────────────────────────
    parts = [f"Symbol: {symbol}"]
    if price       is not None: parts.append(f"Price: ${price:.2f}")
    if change_pct  is not None: parts.append(f"24h change: {change_pct:+.3f}%")
    if reddit_score is not None: parts.append(f"Reddit sentiment: {reddit_score:+.3f}")
    if futures_ls  is not None: parts.append(f"Futures L/S ratio: {futures_ls:+.3f}")
    if fear_greed  is not None: parts.append(f"Fear & Greed: {(fear_greed + 1) * 50:.0f}/100")
    parts.append("Return exactly one float between -1.0 and +1.0. No words.")

    messages = [
        {"role": "system", "content": _SENTIMENT_SYSTEM.format(symbol=symbol)},
        {"role": "user",   "content": "\n".join(parts)},
    ]

    candidates: list[tuple[str, str, str]] = []  # (base_url, api_key, model)
    if _groq_key:
        candidates.append((GROQ_BASE_URL, _groq_key, GROQ_MODEL))
    for model in OR_MODELS:
        if _or_key:
            candidates.append((OR_BASE_URL, _or_key, model))

    score = None
    for base_url, api_key, model in candidates:
        text = _call(base_url, api_key, model, messages, max_tokens=8)
        if text is None:
            continue
        match = re.search(r"[-+]?\d+\.?\d*", text)
        if match:
            score = max(-1.0, min(1.0, float(match.group())))
            logger.debug("sentiment %s via [%s] → %.3f", symbol, model, score)
            break

    _SENTIMENT_CACHE[symbol] = (time.time(), score)
    return score


# ── Prompt système validation ─────────────────────────────────────────────────

SYSTEM_PROMPT = """Tu es un trader quantitatif sénior chargé de valider des signaux \
avant exécution. On te fournit un contexte de marché structuré et une action proposée.

Réponds UNIQUEMENT avec un objet JSON sur une seule ligne :
{"approve": true|false, "reason": "<explication concise en 1-2 phrases>", "confidence": 0.0-1.0}

## Règles de refus (approve: false)
- Score de signal faible ou contradictoire (|score| < 0.2 avec momentum opposé)
- Sentiment et momentum fortement divergents (différence > 0.6 en valeur absolue)
- Fear & Greed extrême côté opposé à l'action (F&G < 20 sur un BUY, > 80 sur un SELL)
- Signal long/short ratio en désaccord net avec l'action (ratio < 0.4 sur BUY, > 0.6 sur SELL)
- Combinaison de 3 signaux ou plus contre l'action, même si chacun est modéré

## Règles d'approbation (approve: true)
- Convergence de la majorité des signaux dans le sens de l'action
- Momentum et sentiment alignés avec l'action
- F&G neutre (20-80) ou dans le sens de l'action

## Exemples
Signal: score=0.65, momentum=0.55, sentiment=0.60, fear_greed=62, ls_ratio=0.58, action=BUY
→ {"approve": true, "reason": "Convergence forte sur tous les signaux, F&G neutre favorable.", "confidence": 0.85}

Signal: score=0.30, momentum=-0.45, sentiment=0.25, fear_greed=18, ls_ratio=0.38, action=BUY
→ {"approve": false, "reason": "Momentum négatif et F&G extrême baissier contredisent le BUY.", "confidence": 0.80}

Signal: score=0.55, momentum=0.10, sentiment=-0.05, fear_greed=50, ls_ratio=0.50, action=BUY
→ {"approve": false, "reason": "Momentum faible et sentiment neutre/négatif ne soutiennent pas le BUY.", "confidence": 0.75}
"""

# ── Extraction JSON ───────────────────────────────────────────────────────────

_JSON_RE = re.compile(r"\{[^{}]+\}", re.DOTALL)

_DEFAULT_OK    = {"approve": True,  "reason": "LLM désactivé — approbation par défaut.", "confidence": 0.5}
_DEFAULT_FAIL  = {"approve": False, "reason": "Réponse LLM non parsable — rejet par sécurité.", "confidence": 0.3}
_DEFAULT_ERROR = {"approve": False, "reason": "Tous les providers indisponibles — rejet par sécurité.", "confidence": 0.3}


def _parse_response(text: str) -> dict[str, Any]:
    start = text.find("{")
    end   = text.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(text[start : end + 1])
            return {
                "approve":    bool(data.get("approve", True)),
                "reason":     str(data.get("reason", "")),
                "confidence": float(data.get("confidence", 0.5)),
            }
        except json.JSONDecodeError:
            pass
    match = _JSON_RE.search(text)
    if match:
        try:
            data = json.loads(match.group())
            return {
                "approve":    bool(data.get("approve", True)),
                "reason":     str(data.get("reason", "")),
                "confidence": float(data.get("confidence", 0.5)),
            }
        except json.JSONDecodeError:
            pass
    return _DEFAULT_FAIL


def _format_signal(signal: dict[str, Any], action: str) -> str:
    def fmt(v: Any) -> str:
        if v is None:   return "N/A"
        if isinstance(v, float): return f"{v:+.3f}"
        return str(v)

    return "\n".join([
        "## Contexte marché",
        f"- Symbole       : {signal.get('symbol', 'N/A')}",
        f"- Prix          : {fmt(signal.get('price'))}",
        f"- Score global  : {fmt(signal.get('score'))}",
        f"- Momentum      : {fmt(signal.get('momentum'))}",
        f"- Sentiment LLM : {fmt(signal.get('sentiment'))}",
        f"- Reddit        : {fmt(signal.get('reddit'))}",
        f"- Fear & Greed  : {fmt(signal.get('fear_greed'))}  (0=peur extrême, 100=avidité extrême)",
        f"- L/S ratio     : {fmt(signal.get('ls_ratio'))}  (>0.5 = majorité long)",
        f"- Taker pression: {fmt(signal.get('taker_pressure'))}",
        "",
        f"## Action proposée : {action.upper()}",
        "",
        'Réponds uniquement en JSON : {"approve": bool, "reason": str, "confidence": float}',
    ])


# ── Agent ─────────────────────────────────────────────────────────────────────

class OpenRouterAgent:
    """Valide un signal de trading via LLM.

    Provider order : Groq (primary) → OpenRouter DeepSeek → OpenRouter Llama

    Usage minimal (Groq seul) :
        configure(api_key="gsk_...")
        agent = OpenRouterAgent()

    Usage complet :
        configure(api_key="gsk_...", openrouter_api_key="sk-or-...")
        agent = OpenRouterAgent()

    Rétrocompat (ancienne signature) :
        agent = OpenRouterAgent(api_key="sk-or-...", model="...", temperature=0.1)
    """

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        temperature: float = 0.1,
    ):
        self.temperature = temperature
        # Rétrocompat : clé passée directement au constructeur
        if api_key:
            configure(api_key=api_key)

    @property
    def enabled(self) -> bool:
        return bool(_groq_key or _or_key)

    def validate(self, signal: dict[str, Any], action: str) -> dict[str, Any]:
        """Retourne {'approve': bool, 'reason': str, 'confidence': float}."""
        if not self.enabled:
            return _DEFAULT_OK

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": _format_signal(signal, action)},
        ]

        # Ordre de tentative : Groq → OR DeepSeek → OR Llama
        candidates: list[tuple[str, str, str]] = []
        if _groq_key:
            candidates.append((GROQ_BASE_URL, _groq_key, GROQ_MODEL))
        for model in OR_MODELS:
            if _or_key:
                candidates.append((OR_BASE_URL, _or_key, model))

        for base_url, api_key, model in candidates:
            text = _call(
                base_url, api_key, model, messages,
                max_tokens=120,
                temperature=self.temperature,
                timeout=15,
            )
            if text is None:
                continue
            result = _parse_response(text)
            logger.debug(
                "[%s] %s %s → approve=%s conf=%.2f : %s",
                model, signal.get("symbol", "?"), action,
                result["approve"], result["confidence"], result["reason"],
            )
            return result

        return _DEFAULT_ERROR