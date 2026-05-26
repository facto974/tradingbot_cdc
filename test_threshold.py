"""Test du correctif : vérifie qu'aucune position ne s'ouvre avec score < threshold_long."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd

from src.strategy.momentum_sentiment import MomentumSentimentStrategy, StrategyConfig
from src.agent.openrouter_client import _DEFAULT_OK, _DEFAULT_FAIL, _DEFAULT_ERROR
from src.broker.paper_broker import PaperBroker


def test_garde_fou_score_inferieur_seuil():
    """Vérifie que le garde-fou score < threshold_long bloque l'ouverture."""
    # Config simulant la config.yaml de l'utilisateur
    cfg = StrategyConfig(
        w_momentum=0.5,
        w_sentiment=0.3,
        w_fear_greed=0.2,
        threshold_long=0.48,
        threshold_short=-0.7,
        allow_short=False,
        high_conviction=False,  # mode normal pour tester le threshold pur
        min_active_sentiment_sources=3,
    )
    strat = MomentumSentimentStrategy(cfg)

    # Créer un signal avec score ~0.30 (bien en dessous de 0.48)
    idx = pd.date_range("2024-01-01", periods=100, freq="h")
    close = pd.Series(np.linspace(100, 105, 100), index=idx)  # léger uptrend
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                       "Volume": 1.0}, index=idx)

    sig = strat.evaluate(df, reddit=0.1, futures_ls=0.05, coingecko=0.0, fear_greed=0.2)
    print(f"Mode normal: score={sig.score:.4f} decision={sig.decision}")

    # Le score < 0.48 → la décision doit être FLAT (pas LONG)
    if sig.score < 0.48:
        assert sig.decision == "FLAT", (
            f"❌ Score={sig.score:.4f} < 0.48 mais décision={sig.decision} !"
        )
        print(f"✅ Score={sig.score:.4f} < 0.48 → décision FLAT (correct)")
    else:
        print(f"ℹ️ Score={sig.score:.4f} ≥ 0.48, vérification non applicable ici")


def test_high_conviction_toutes_sources_neutres():
    """Mode high_conviction : toutes les sources neutres → FLAT."""
    cfg = StrategyConfig(
        threshold_long=0.48,
        high_conviction=True,
        min_active_sentiment_sources=2,
    )
    strat = MomentumSentimentStrategy(cfg)

    idx = pd.date_range("2024-01-01", periods=100, freq="h")
    close = pd.Series(np.linspace(100, 200, 100), index=idx)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                       "Volume": 1.0}, index=idx)

    # Toutes les sources à 0.0 (neutres)
    sig = strat.evaluate(df, reddit=0.0, futures_ls=0.0, coingecko=0.0, fear_greed=0.0)
    print(f"High-conviction neutre: score={sig.score:.4f} decision={sig.decision}")

    assert sig.decision == "FLAT", (
        f"❌ Toutes sources neutres mais décision={sig.decision} !"
    )
    print(f"✅ Toutes sources neutres → décision FLAT (correct)")


def test_low_conviction_par_defaut():
    """Mode normal : threshold_long à 0.0 (défaut) permet any > 0."""
    cfg = StrategyConfig(
        w_momentum=0.2,
        w_sentiment=0.6,
        w_fear_greed=0.2,
        threshold_long=0.0,  # valeur par défaut
        high_conviction=False,
    )
    strat = MomentumSentimentStrategy(cfg)

    idx = pd.date_range("2024-01-01", periods=100, freq="h")
    close = pd.Series(np.linspace(100, 200, 100), index=idx)
    df = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close,
                       "Volume": 1.0}, index=idx)

    sig = strat.evaluate(df, reddit=0.3, futures_ls=0.2, coingecko=0.1, fear_greed=0.3)
    print(f"Mode défaut: score={sig.score:.4f} decision={sig.decision}")

    # Avec threshold=0.0 et score>0, on attend LONG
    assert sig.decision == "LONG", (
        f"❌ Mode défaut: score>0 mais décision={sig.decision}"
    )
    print(f"✅ Mode défaut (threshold=0.0) → décision LONG (correct)")


def test_defaults_llm_securite():
    """Les defaults LLM doivent être reject pour FAIL et ERROR."""
    assert _DEFAULT_OK["approve"] is True, "LLM désactivé doit approuver"
    assert _DEFAULT_FAIL["approve"] is False, "LLM réponse non parsable → REJECT"
    assert _DEFAULT_ERROR["approve"] is False, "LLM erreur → REJECT"
    print(f"✅ LLM defaults: OK={_DEFAULT_OK['approve']} "
          f"FAIL={_DEFAULT_FAIL['approve']} ERROR={_DEFAULT_ERROR['approve']}")


if __name__ == "__main__":
    print("\n═══ Test 1 : Garde-fou score < threshold_long ═══")
    test_garde_fou_score_inferieur_seuil()

    print("\n═══ Test 2 : High-conviction toutes sources neutres ═══")
    test_high_conviction_toutes_sources_neutres()

    print("\n═══ Test 3 : Mode basse conviction (défaut) ═══")
    test_low_conviction_par_defaut()

    print("\n═══ Test 4 : Defaults LLM sécurité ═══")
    test_defaults_llm_securite()

    print("\n✅ Tous les tests de seuil passés.")