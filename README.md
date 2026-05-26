# TradingBotCDC-Trader

Trading agentique automatisé sur **CryptoCom Exchange** via le **Model Context Protocol (MCP)**, avec stratégie hybride **momentum + sentiment social**, backtesting vectorisé, paper-trading sandbox, et stack d'observabilité **Prometheus + Grafana** prête à l'emploi.

> Stack 100% gratuite : seuls les frais réels de trading CryptoCom s'appliquent en mode live.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      VSCode + Cline                          │
│                  └── OpenRouter (free tier)                  │
└──────────────────────────────────────────────────────────────┘
                             │
            ┌────────────────┼────────────────┐
            ▼                ▼                ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │  DATA LAYER  │  │  SENTIMENT   │  │  EXECUTION   │
   │              │  │              │  │              │
   │ • yfinance   │  │ • Reddit PRAW│  │  CryptoCom MCP  │
   │ • Finnhub    │  │ • Fear&Greed │  │   ├─ sandbox │
   │ • CoinGecko  │  │ • StockTwits │  │   └─ live    │
   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
          └─────────────────┼─────────────────┘
                            ▼
                 ┌────────────────────┐
                 │   Strategy Engine  │
                 │  Momentum+Sentiment│
                 └─────────┬──────────┘
                           ▼
                 ┌────────────────────┐
                 │  SQLite + Metrics  │──► Prometheus ──► Grafana
                 └────────────────────┘
```

---

## Roadmap (vos étapes)

| Étape | Stack | Statut |
|-------|-------|--------|
| 1 — Développement | VSCode + Cline + OpenRouter | ✔ supporté |
| 2 — Backtesting   | yfinance / CoinGecko (historique) | ✔ `run_backtest.py` |
| 3 — Paper trading | CryptoCom Sandbox (`api.sandbox.cryptocom.com`) | ✔ `run_paper.py` |
| 4 — Live          | CryptoCom réel | ✔ basculer `sandbox: false` |

---

## Prérequis

- Python 3.10+
- Docker & docker-compose (pour Grafana/Prometheus)
- Comptes/clés (toutes free tier) :
  - **CryptoCom API** sandbox → https://exchange.sandbox.cryptocom.com/
  - **OpenRouter** → https://openrouter.ai/keys (modèles free)
  - **Finnhub** (optionnel) → https://finnhub.io
  - **Reddit PRAW** (optionnel) → https://www.reddit.com/prefs/apps

---

## Installation

```bash
git clone <ce-repo> cryptocom_trading_agent
cd cryptocom_trading_agent
python -m venv venv && source venv/bin/activate     # Linux/Mac
# venv\Scripts\activate                              # Windows
pip install -r requirements.txt
cp .env.example .env                                 # éditez vos clés
cp config.example.yaml config.yaml                   # tunez vos paramètres
```

---

## Lancer la stack observabilité (Grafana + Prometheus)

```bash
docker-compose up -d prometheus grafana
```

- Prometheus : http://localhost:9090
- Grafana    : http://localhost:3000 (admin / admin)

Les dashboards `Trading PnL`, `Sentiment` et `Risk` sont auto-provisionnés depuis `grafana/dashboards/`.

L'exporter de l'agent expose les métriques sur `http://localhost:8000/metrics`.

---

## Backtesting (étape 2)

```bash
python run_backtest.py --symbol BTC-USD --start 2023-01-01 --end 2024-12-31
```

Sortie console : Sharpe, max drawdown, total return, nb trades. Equity curve sauvegardée dans `data/backtest_<symbol>.csv`.

---

## Paper trading sandbox (étape 3)

```bash
python run_paper.py
```

L'agent boucle toutes les `loop_interval` secondes (défaut 60 s) :
1. fetch OHLCV + sentiment
2. calcule signal hybride momentum/sentiment
3. demande validation au LLM (OpenRouter free tier)
4. envoie ordres via REST CryptoCom sandbox
5. persiste trades + équité dans SQLite et publie métriques

---

## Serveur MCP (skills exposés à un agent IA externe)

```bash
python run_mcp.py
```

Tools exposés :
- `get_market_snapshot(symbol)`
- `get_sentiment(symbol)`
- `place_order(symbol, side, qty, type)`
- `get_positions()`
- `get_pnl()`
- `run_backtest(symbol, start, end)`

Compatible Claude Desktop / Cline / Cursor via `stdio`. Configuration MCP type :

```json
{
  "mcpServers": {
    "cryptocom-trader": {
      "command": "python",
      "args": ["/chemin/absolu/run_mcp.py"]
    }
  }
}
```

---

## Live trading (étape 4)

1. Vérifiez que la stratégie est rentable en backtest **et** paper trading sur ≥ 30 jours.
2. Dans `config.yaml` : `sandbox: false`.
3. Mettez vos vraies clés `CRYPTOCOM_API_KEY` / `CRYPTOCOM_API_SECRET` dans `.env`.
4. Démarrez avec un `max_position_usd` réduit (ex : 50 $).

> ⚠ Aucune garantie de profit. Trader implique un risque de perte en capital.

---

## Stratégie : Momentum + Sentiment hybride

Score composite entre -1 et +1 :

```
score = w_mom * momentum_z + w_sent * sentiment_z + w_fg * fear_greed_z
```

- `momentum_z`  : z-score du rendement 7 j (lissé EMA 24h)
- `sentiment_z` : z-score Reddit + StockTwits (bullish/bearish ratio)
- `fear_greed_z`: z-score de l'index Fear & Greed crypto

Décision :
- `score > +threshold_long`  → LONG (taille = `kelly_fraction * equity`)
- `score < -threshold_short` → CLOSE / SHORT (si `allow_short: true`)
- sinon → flat

Tous les paramètres sont dans `config.yaml`.

---

## Licence

MIT — gratuit, sans garantie.
