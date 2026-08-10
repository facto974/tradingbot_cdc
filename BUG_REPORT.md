# Rapport de bugs — tradingbot_cdc

## Résumé exécutif
Le bot **n'est pas rentable** pour 3 raisons principales :
1. **Stop-loss inefficace** : utilise le prix OHLCV 1h (pas de spot) → perte MATIC -$57 (-78%)
2. **Equity mal calculée** : `PaperBroker.equity()` est incorrect pour les shorts → P&L affiché erroné
3. **Stratégie trop restrictive** : `require_aligned` + `close_threshold @ 0.00` bloquent des trades rentables

---

## 🐛 Bug 1 — Stop-loss sur OHLCV au lieu de spot (CRITIQUE)
**Fichier** : `src/agent/trading_agent.py` ligne 474
**Problème** : `snap.price` vient de l'OHLCV (bougie 1h), pas d'un prix spot.
**Conséquence** : Si le prix chute entre deux bougies horaires, le SL ne voit rien. C'est ce qui a détruit le trade MATIC (-$57).
**Correction** : Ajouter un fetch spot price depuis Binance dans la boucle SL.

## 🐛 Bug 2 — `PaperBroker.equity()` incorrect pour les shorts (CRITIQUE)
**Fichier** : `src/broker/paper_broker.py` ligne 110
**Problème** : `market_value = sum(p.qty * marks.get(s, p.avg_price))` donne une valeur négative pour les positions short (qty négative).
**Exemple** : Short 10 BTC à $30k, BTC monte à $35k → `market_value = -10 × 35k = -$350k` au lieu de `-10 × (35k - 30k) = -$50k`. L'equity s'affiche fausse.
**Correction** : Remplacer par `abs(p.qty) * marks.get(s, p.avg_price)`.

## 🐛 Bug 3 — Pas de validation cash dans paper broker
**Fichier** : `src/broker/paper_broker.py` lignes 41, 50, 62, 72, 81, 93
**Problème** : Aucun check avant `self.cash -= notional + fee`. Si cash = $10 et que l'ordre vaut $30, cash devient -$22. Le broker permet de trader avec de l'argent qu'on n'a pas.
**Correction** : Ajouter `if notional + fee > self.cash: raise ValueError(...)`.

## 🐛 Bug 4 — `close_threshold = 0.00` trop bas
**Fichier** : `src/strategy/momentum_sentiment.py` ligne 43 / `config.yaml`
**Problème** : Le seuil de fermeture LONG est à 0.00. Une position ne se ferme que si le score devient strictement négatif. Si le score reste entre 0.00 et 0.08, la position reste HOLD indéfiniment (jusqu'au time-based exit à 48h).
**Correction** : Baisser `close_threshold` à -0.05 (ou -0.10) pour fermer plus tôt.

## 🐛 Bug 5 — `require_aligned` bloque les trades unidirectionnels
**Fichier** : `src/strategy/momentum_sentiment.py` lignes 192-199
**Problème** : Si sentiment est négatif mais momentum positif (ou vice-versa), la décision reste FLAT même si le score composite est très haut. En pratique, les sources sentiment sont souvent absentes (None) → le bloc sentiment est ignoré et seul le momentum compte. Mais si les deux sont présents et divergents, aucun trade n'ouvre.
**Correction** : Ajouter un flag pour désactiver l'alignement strict, ou ne l'appliquer que quand les 2 sources sont significatives (abs > 0.20).

## 🐛 Bug 6 — `enable_trend_filter` peut bloquer tous les LONG
**Fichier** : `src/strategy/momentum_sentiment.py` lignes 139-146
**Problème** : Si SMA rapide < SMA lente → pas de LONG. En bear market, tous les LONG sont bloqués quel que soit le score. C'est voulu mais peut être trop restrictif.
**Correction** : Déjà désactivé (`false` dans config.yaml). RAS si on veut le garder.

## 🐛 Bug 7 — Données CryptoCom SSL échouent sans fallback prix spot
**Fichier** : `src/data/aggregator.py` lignes 216-224
**Problème** : Les appels à `binance_client` et `cryptocom_client` peuvent échouer avec SSL error. Le prix provient alors de l'OHLCV (cache ou fetch). Si le prix OHLCV est celui d'il y a 1h, le SL/TP utilisent un prix périmé.
**Correction** : Ajouter un fetch spot price explicite pour le SL, hors OHLCV.

## 🐛 Bug 8 — `_position_size` ne vérifie pas l'equity en temps réel
**Fichier** : `src/agent/trading_agent.py` ligne 398
**Problème** : Calcule `min(equity * 0.25, max_position_usd)` sans vérifier si le cash disponible suffit. Si plusieurs trades s'ouvrent, le dernier peut dépasser le cash.
**Correction** : Aucun check cash → à ajouter dans `_process_symbol` avant l'exécution.

---

## Conclusion : pourquoi le bot n'est-il pas rentable ?

1. **Le stop-loss n'a pas fonctionné** sur la perte de -57$ MATIC (OHLCV au lieu de spot).
2. **L'equity affichée est fausse** (shorts mal calculés dans paper_broker).
3. **La stratégie est trop conservatrice** : `require_aligned` et `close_threshold=0.00` ferment peu de trades, permettant aux pertes de s'accumuler.
4. **Pas de limite cash** : le broker permet d'acheter sans fonds suffisants (crédit virtuel), faussant les performances.

## Plan de correction
1. ✅ `paper_broker.py` : corriger `equity()` pour les shorts + ajouter validation cash
2. ✅ `trading_agent.py` : ajouter prix spot Binance dans la boucle SL
3. ✅ `config.yaml` : `close_threshold: -0.05` pour fermer les positions plus tôt
4. ✅ `config.yaml` : `require_aligned: false` pour ne pas bloquer les trades unidirectionnels