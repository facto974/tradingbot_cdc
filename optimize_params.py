"""Optimisation des paramètres — sweep réduit (20 combinaisons).
Exécute le backtest en direct (pas de subprocess)."""
import yaml, itertools, json, sys, os, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

BASE_CFG = yaml.safe_load(Path("config.yaml").read_text())

PARAMS = {
    "threshold_long":  [0.20, 0.25, 0.30, 0.35, 0.40, 0.45],
    "threshold_short": [-0.20, -0.25, -0.30, -0.35, -0.40, -0.45],
}

results = []

# Combinaisons
pairs = [(tl, ts) for tl in PARAMS["threshold_long"] for ts in PARAMS["threshold_short"]][:20]

print(f"Test de {len(pairs)} combinaisons...\n")

for i, (tl, ts) in enumerate(pairs, 1):
    # Copie profonde
    cfg = yaml.safe_load(yaml.safe_dump(BASE_CFG))
    cfg["strategy"]["thresholds"]["long"]  = tl
    cfg["strategy"]["thresholds"]["short"] = ts
    cfg["risk"]["initial_capital"] = 100
    cfg["risk"]["max_position_usd"] = 20
    cfg["risk"]["stop_loss_pct"]    = 0.03
    cfg["risk"]["take_profit_pct"]   = 0.06
    cfg["risk"]["kelly_fraction"]   = 0.25
    cfg["risk"]["allow_short"]      = False
    cfg["mode"] = "paper"

    # Sauvegarder temporairement
    tmp = Path(f"/tmp/backtest_cfg_{i}.yaml")
    tmp.parent.mkdir(exist_ok=True)
    yaml.safe_dump(cfg, open(tmp, "w"))

    from src.config import Settings
    
    t0 = time.time()
    
    # Rediriger stderr pour éviter les warnings
    import warnings
    warnings.filterwarnings("ignore")
    
    from src.data.yfinance_client import fetch_ohlcv
    from src.strategy.momentum_sentiment import MomentumSentimentStrategy, StrategyConfig
    
    try:
        df = fetch_ohlcv("BTC-USD", start="2023-01-01", end=None, interval="1h")
        if df.empty:
            print(f"[{i}/{len(pairs)}] tl={tl:.2f} ts={ts:.2f} | PAS DE DONNEES")
            continue
        
        # Charger la config directement
        from src.backtest.engine import run
        
        # Créer StrategyConfig à partir du dict
        c = cfg.get("strategy", {})
        w = c.get("weights", {})
        m = c.get("momentum", {})
        t = c.get("thresholds", {})
        s = c.get("sentiment", {})
        r = cfg.get("risk", {})
        
        sc = StrategyConfig(
            w_momentum=w.get("momentum", 0.50),
            w_sentiment=w.get("sentiment", 0.30),
            w_fear_greed=w.get("fear_greed", 0.20),
            lookback=m.get("lookback_days", 14),
            ema_smooth=m.get("ema_smooth", 12),
            threshold_long=t.get("long", 0.20),
            threshold_short=t.get("short", -0.20),
            allow_short=r.get("allow_short", False),
            high_conviction=s.get("high_conviction", False),
            min_active_sentiment_sources=s.get("min_active_sources", 2),
        )
        strat = MomentumSentimentStrategy(sc)
        res = run(df, strat)
        
        elapsed = time.time() - t0
        r = {
            "tl": tl, "ts": ts,
            "return": round(res.total_return * 100, 2),
            "sharpe": round(res.sharpe, 2),
            "dd": round(res.max_dd * 100, 2),
            "trades": res.trades,
            "win": round(res.win_rate * 100, 2),
        }
        results.append(r)
        print(f"[{i}/{len(pairs)}] tl={tl:.2f} ts={ts:.2f} | "
              f"ret={r['return']:+.2f}% sharpe={r['sharpe']:.2f} "
              f"dd={r['dd']:.2f}% trades={r['trades']} win={r['win']:.1f}% "
              f"tiemsp={elapsed:.0f}s")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"[{i}/{len(pairs)}] tl={tl:.2f} ts={ts:.2f} | ECHEC: {e} ({elapsed:.0f}s)")
    finally:
        tmp.unlink(missing_ok=True)

if results:
    out_path = Path("data/optimization_results.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    
    # Top 5 Sharpe
    by_sharpe = sorted(results, key=lambda x: x.get("sharpe", -999), reverse=True)
    print("\nTOP 5 par Sharpe:")
    for r in by_sharpe[:5]:
        print(f"   tl={r['tl']:.2f} ts={r['ts']:.2f} sharpe={r['sharpe']:.2f} "
              f"ret={r['return']:+.2f}% win={r['win']:.1f}% trades={r['trades']}")
    
    # Top 5 return
    by_ret = sorted(results, key=lambda x: x.get("return", -999), reverse=True)
    print("\nTOP 5 par rendement:")
    for r in by_ret[:5]:
        print(f"   tl={r['tl']:.2f} ts={r['ts']:.2f} return={r['return']:+.2f}% "
              f"sharpe={r['sharpe']:.2f} win={r['win']:.1f}% trades={r['trades']}")
    
    # Best risk/reward
    def risk_score(r):
        dd = max(r.get("dd", 1), 1)
        return r.get("return", 0) * r.get("win", 0) / dd
    best_risk = max(results, key=risk_score)
    print("\nMEILLEUR risque/rendement:")
    print(f"   tl={best_risk['tl']:.2f} ts={best_risk['ts']:.2f} "
          f"sharpe={best_risk['sharpe']:.2f} ret={best_risk['return']:+.2f}% "
          f"win={best_risk['win']:.1f}% trades={best_risk['trades']}")
    
    print(f"\n{len(results)} combinaisons -> data/optimization_results.json")
else:
    print("\nAucun resultat")