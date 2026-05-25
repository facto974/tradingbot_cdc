"""Stratégie hybride momentum + sentiment avec re-pondération dynamique.

Score composite ∈ [-1, 1] :
    score = w_mom  * mom_score
          + w_sent * sentiment_avg(sources DISPONIBLES uniquement)
          + w_fg   * fear_greed (si dispo)

Re-pondération dynamique :
    Si une source de sentiment est indisponible (None), elle est exclue de la
    moyenne et les poids des composantes restantes sont renormalisés pour
    totaliser 1.0.

Améliorations v2 :
    - Seuil de fermeture distinct (close_threshold) pour éviter les allers-retours
    - Alignement momentum + sentiment requis pour ouvrir (require_aligned)
    - Poids momentum augmenté (0.40), sentiment réduit (0.40)
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind


# ── Configuration ─────────────────────────────────────────────

@dataclass
class StrategyConfig:
    w_momentum:   float = 0.35
    w_sentiment:  float = 0.45
    w_fear_greed: float = 0.20

    lookback:   int = 14        # jours/barres pour le calcul du momentum
    ema_smooth: int = 24        # EMA lissage du prix

    threshold_long:  float = 0.0    # score >= 0 → LONG
    threshold_short: float = -0.30  # score <= -0.30 → SHORT
    close_threshold: float = -0.10  # score < -0.10 ferme une position LONG

    allow_short:     bool  = False

    # Mode "high-conviction" : exiger N sources actives ET unanimes
    high_conviction: bool = False
    min_active_sentiment_sources: int = 4

    # Alignement obligatoire momentum + sentiment pour ouvrir
    require_aligned: bool = True
    # Seuil minimum de momentum en valeur absolue pour considérer un signal
    min_momentum_abs: float = 0.10

    # Poids relatifs des sous-sources dans le bloc "sentiment"
    sentiment_weights: dict[str, float] | None = None


DEFAULT_SENT_WEIGHTS: dict[str, float] = {
    "binance_change": 1.5,
    "binance_taker":  1.2,
    "reddit":         1.0,
    "futures_ls":     0.8,
    "coingecko":      0.5,
}


# ── Signal ────────────────────────────────────────────────────

@dataclass
class Signal:
    score:      float
    momentum:   float
    sentiment:  float
    fear_greed: float
    decision:   str           # 'LONG' | 'SHORT' | 'FLAT' | 'HOLD'
    active_sources: list[str] = field(default_factory=list)
    # Indique si l'alignement momentum/sentiment est valide
    aligned: bool = True


# ── Helpers ───────────────────────────────────────────────────

def _weighted_avg(
    values:  dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    """Moyenne pondérée des sources non-None.

    Retourne None si toutes les sources sont None (pas 0.0, pour ne pas
    biaiser le score composite).
    """
    num = 0.0
    den = 0.0

    for k, v in values.items():
        if v is None:
            continue
        w    = float(weights.get(k, 1.0))
        num += w * float(v)
        den += w

    return (num / den) if den > 0.0 else None


# ── Stratégie ─────────────────────────────────────────────────

class MomentumSentimentStrategy:
    def __init__(self, cfg: StrategyConfig):
        self.cfg          = cfg
        self.sent_weights = {**DEFAULT_SENT_WEIGHTS, **(cfg.sentiment_weights or {})}
        # Mémoire des décisions précédentes pour éviter les allers-retours
        self._prev_decision: str = "FLAT"

    # ── API live : prend un snapshot agrégé ───────────────────

    def evaluate(
        self,
        ohlcv:          pd.DataFrame,
        reddit:         float | None,
        futures_ls:     float | None,
        coingecko:      float | None,
        fear_greed:     float | None,
        binance_change: float | None = None,
        binance_taker:  float | None = None,
    ) -> Signal:
        """Calcule le signal live avec re-pondération dynamique des sources."""

        # ── Momentum ──────────────────────────────────────────
        if ohlcv is None or ohlcv.empty or "Close" not in ohlcv.columns:
            mom_score = None
            mom_raw   = 0.0
        else:
            close      = ind.ema(ohlcv["Close"], self.cfg.ema_smooth)
            mom_series = ind.momentum(close, self.cfg.lookback)
            mom_raw    = float(mom_series.iloc[-1]) if not mom_series.empty else 0.0
            z          = ind.zscore(mom_series).iloc[-1] if not mom_series.empty else 0.0
            mom_score  = float(np.tanh(float(z) if not pd.isna(z) else 0.0))

        # ── Sentiment composite (None → exclusion) ────────────
        sent_inputs: dict[str, float | None] = {
            "binance_change": binance_change,
            "binance_taker":  binance_taker,
            "reddit":         reddit,
            "futures_ls":     futures_ls,
            "coingecko":      coingecko,
        }
        sentiment_avg = _weighted_avg(sent_inputs, self.sent_weights)
        active_sent   = [k for k, v in sent_inputs.items() if v is not None]

        # ── Re-pondération dynamique des 3 blocs ─────────────
        components: dict[str, tuple[float, float]] = {}
        if mom_score is not None:
            components["momentum"]   = (mom_score,     self.cfg.w_momentum)
        if sentiment_avg is not None:
            components["sentiment"]  = (sentiment_avg, self.cfg.w_sentiment)
        if fear_greed is not None:
            components["fear_greed"] = (fear_greed,    self.cfg.w_fear_greed)

        if not components:
            return Signal(0.0, mom_raw, 0.0, 0.0, "FLAT")

        total_w = sum(w for _, w in components.values())
        if total_w <= 0:
            return Signal(0.0, mom_raw, sentiment_avg or 0.0, fear_greed or 0.0,
                          "FLAT", active_sources=active_sent)

        score = max(-1.0, min(1.0,
            sum(v * (w / total_w) for v, w in components.values())
        ))

        # ── Vérification alignement momentum ↔ sentiment ─────
        aligned = True
        if self.cfg.require_aligned and mom_score is not None and sentiment_avg is not None:
            mom_sign = np.sign(mom_score)
            sent_sign = np.sign(sentiment_avg)
            # Si les deux signaux sont significatifs et opposés → pas aligné
            if abs(mom_score) >= self.cfg.min_momentum_abs and abs(sentiment_avg) >= self.cfg.min_momentum_abs:
                if mom_sign != sent_sign:
                    aligned = False

        # ── Mode "high-conviction" ─────────────────────────
        if self.cfg.high_conviction and sentiment_avg is not None:
            n_active = len(active_sent)
            if n_active < self.cfg.min_active_sentiment_sources:
                decision = "FLAT"
            else:
                signs = set()
                for k in active_sent:
                    v = sent_inputs[k]
                    if v is not None:
                        if v > 0.02:
                            signs.add("LONG")
                        elif v < -0.02:
                            signs.add("SHORT")
                        else:
                            signs.add("NEUTRAL")
                signs.discard("NEUTRAL")
                if len(signs) > 1:
                    decision = "FLAT"
                elif len(signs) == 1:
                    d = signs.pop()
                    if d == "LONG" and score >= self.cfg.threshold_long:
                        decision = "LONG" if aligned else "FLAT"
                    elif d == "SHORT" and score <= self.cfg.threshold_short:
                        decision = "SHORT" if aligned else "FLAT"
                    else:
                        decision = "FLAT"
                else:
                    decision = "FLAT"

        else:
            # ── Mode normal avec close_threshold ────────────
            # Décision d'ouverture
            if self._prev_decision == "LONG" and score > self.cfg.close_threshold:
                # Garder la position ouverte si le score n'a pas assez baissé
                decision = "HOLD"
            elif score >= self.cfg.threshold_long:
                # Ouverture LONG si aligné
                if aligned or not self.cfg.require_aligned:
                    decision = "LONG"
                else:
                    decision = "FLAT"
            elif score <= self.cfg.threshold_short and self.cfg.allow_short:
                if aligned or not self.cfg.require_aligned:
                    decision = "SHORT"
                else:
                    decision = "FLAT"
            else:
                decision = "FLAT"

        # Mémoriser la décision pour le prochain cycle
        if decision in ("LONG", "SHORT", "FLAT"):
            self._prev_decision = decision

        all_active = (
            (["momentum"] if mom_score is not None else [])
            + active_sent
            + (["fear_greed"] if fear_greed is not None else [])
        )

        return Signal(
            score=score,
            momentum=mom_raw,
            sentiment=float(sentiment_avg or 0.0),
            fear_greed=float(fear_greed or 0.0),
            decision=decision,
            active_sources=all_active,
            aligned=aligned,
        )

    # ── API vectorisée pour backtest ──────────────────────────

    def vectorized_signals(
        self,
        ohlcv:             pd.DataFrame,
        sentiment_series:  pd.Series | None = None,
        fear_greed_series: pd.Series | None = None,
    ) -> pd.DataFrame:
        """Calcul vectorisé pour backtest (poids fixes, séries complètes)."""
        close     = ind.ema(ohlcv["Close"], self.cfg.ema_smooth)
        mom       = ind.momentum(close, self.cfg.lookback)
        z         = ind.zscore(mom)
        mom_score = np.tanh(z.fillna(0.0))

        sentiment = (
            (sentiment_series.reindex(ohlcv.index).ffill().fillna(0.0))
            if sentiment_series is not None
            else pd.Series(0.0, index=ohlcv.index)
        )
        fg = (
            (fear_greed_series.reindex(ohlcv.index).ffill().fillna(0.0))
            if fear_greed_series is not None
            else pd.Series(0.0, index=ohlcv.index)
        )

        score = (
            self.cfg.w_momentum   * mom_score
            + self.cfg.w_sentiment  * sentiment
            + self.cfg.w_fear_greed * fg
        ).clip(-1, 1)

        # Alignement momentum ↔ sentiment pour vectorized
        aligned = (
            (np.sign(mom_score) == np.sign(sentiment))
            | (abs(mom_score) < self.cfg.min_momentum_abs)
            | (abs(sentiment) < self.cfg.min_momentum_abs)
        ) if self.cfg.require_aligned else pd.Series(True, index=ohlcv.index)

        position = pd.Series(0, index=ohlcv.index, dtype=int)

        # Ouverture LONG : score >= threshold ET aligné
        open_long = (score >= self.cfg.threshold_long) & aligned
        position[open_long] = 1

        # Fermeture LONG : score < close_threshold
        close_long = (score < self.cfg.close_threshold) & (position.shift(1) == 1)
        position[close_long] = 0

        if self.cfg.allow_short:
            open_short = (score <= self.cfg.threshold_short) & aligned
            position[open_short & (position.shift(1) != 1)] = -1
            close_short = (score > -self.cfg.close_threshold) & (position.shift(1) == -1)
            position[close_short] = 0

        # Forward-fill les positions pour garder la position tant qu'elle n'est pas fermée
        position = position.replace(0, method="ffill").fillna(0)

        return pd.DataFrame({
            "score":      score,
            "momentum":   mom_score,
            "sentiment":  sentiment,
            "fear_greed": fg,
            "aligned":    aligned,
            "position":   position,
        })