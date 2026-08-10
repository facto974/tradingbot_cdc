# Changement de sizing — 25% fixe

## Date
2026-07-28

## Pourquoi
Le sweep `sweep_sizing.py` a comparé plusieurs allocations et l'allocation fixe **25% par position** est la plus performante sur la période testée :

| Sizing | Return | Sharpe | DD | Win% | Final |
|--------|--------|--------|----|------|-------|
| 33%/position (fixe) | -10.30% | -1.73 | -21.62% | 40.9% | 89.70 |
| **25%/position (fixe)** | **+13.48%** | **2.61** | **-16.59%** | **45.9%** | **113.48** |
| 20%/position (fixe) | +10.76% | 2.59 | -13.51% | 45.9% | 110.76 |
| 15%/position (fixe) | +8.06% | 2.57 | -10.32% | 45.9% | 108.06 |
| 10%/position (fixe) | +5.36% | 2.54 | -7.02% | 45.9% | 105.36 |
| LIVE (fidèle) | +7.73% | 2.02 | -9.59% | 45.9% | 107.73 |

## Modifications effectuées

### `src/agent/trading_agent.py`
- `_position_size()` : remplacement du sizing dynamique (conviction, night trade, vol, diversification) par un sizing **risk-based** : risque de portefeuille cible = **1% de l'equity**, converti en notionnel via la distance au stop-loss (`qty = risk_target / stop_dist`), plafonné par `min(equity × 0.25, max_position_usd)`.
  - Avec `stop_loss_pct = 4%`, le sizing risk-based équivaut à un sizing fixe de **25% de l'equity** (1% / 4% = 25%).
- Affichage console : format des prix changé de `,.0f` à `,.2f` pour correctement afficher les actifs < $1 (MATIC, DOGE, etc.).

### Config
- Aucun changement de `config.yaml` nécessaire pour activation du sizing fixe : c'est maintenant le comportement par défaut du papier-trading via `run_paper.py`.

## État au moment de la modif
- Base : `data/trader.db`
- Position ouverte : `MATIC-USD` LONG — qty 66.93 @ $0.3794 (ouverte le 2026-07-28 17:53 UTC)
- Cash paper : $74.61
- Equity : $100.00

## Comment revenir en arrière
Si besoin, restaurer l'ancien `_position_size()` qui utilisait conviction/night_mult/vol_factor/diversification_factor.

## Validation
- `sweep_sizing.py` OK
- `run_paper.py` OK (affichage prix corrigé, sizing 25% actif)

## Note : aucun nouveau trade depuis la modif
Le debug (`debug_signals.py`) montre que presque tous les scores sont négatifs et/ou ne passent pas le seuil LONG (`>= 0.08`) ou l’alignement momentum/sentiment. Seul MATIC-USD est positif et reste en `HOLD` car position déjà ouverte. Cela explique qu’aucun nouveau trade ne s’exécute pour l’instant : c’est un comportement attendu compte tenu des conditions de marché et des filtres actifs.
