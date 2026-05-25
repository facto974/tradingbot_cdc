"""Test each data source individually with colored output."""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from src.data.binance_client import price_change_score, taker_pressure_score, price, klines, ticker_24h
from src.data.coingecko_client import community_score as cg_community_score
from src.data.coingecko_client import price as cg_price
from src.data.fear_greed_client import normalized_score
from src.data.reddit_client import RedditClient
from src.data.binance_futures_client import long_short_ratio, funding_rate, top_trader_ratio
from src.data.ohlcv_client import fetch_ohlcv
from src.strategy.momentum_sentiment import MomentumSentimentStrategy, StrategyConfig

SYMBOL = "2Z-USD"

# ── ANSI colors ───────────────────────────────────────────────
C = {
    "blue":   "\033[94m",
    "cyan":   "\033[96m",
    "green":  "\033[92m",
    "yellow": "\033[93m",
    "red":    "\033[91m",
    "grey":   "\033[90m",
    "white":  "\033[97m",
    "bold":   "\033[1m",
    "dim":    "\033[2m",
    "end":    "\033[0m",
}

def ok(msg: str) -> str:
    return f"{C['green']}\u2713{msg}{C['end']}"

def na(msg: str) -> str:
    return f"{C['red']}\u2013{msg}{C['end']}"

def section(title: str) -> None:
    dash = "\u2500"
    n = max(1, 50 - len(title))
    print(f"\n{C['blue']}{C['bold']}{dash}{dash} {title} {C['grey']}{dash * n}{C['end']}")

def show(label: str, value, unit: str = "") -> None:
    if value is None:
        print(f"  {na('')} {C['grey']}{label:<32}{C['end']}{C['red']}NA{C['end']}")
    elif isinstance(value, float):
        color = C['green'] if value > 0.05 else C['red'] if value < -0.05 else C['white']
        val_str = f"{color}{value:+.4f}{C['end']}"
        print(f"  {ok('')} {C['dim']}{label:<32}{C['end']} {val_str}  {unit}")
    elif isinstance(value, int):
        print(f"  {ok('')} {C['dim']}{label:<32}{C['end']} {C['cyan']}{value}{C['end']}  {unit}")
    else:
        print(f"  {ok('')} {C['dim']}{label:<32}{C['end']} {value}  {unit}")

# ══════════════════════════════════════════════════════════════
# HEADER
print(f"\n{C['bold']}{C['cyan']}{'═' * 60}{C['end']}")
print(f"{C['bold']}{C['white']}  TEST DES SOURCES — {SYMBOL}{C['end']}")
print(f"{C['bold']}{C['cyan']}{'═' * 60}{C['end']}")

# ── OHLCV ─────────────────────────────────────────────────────
section("OHLCV")
try:
    df = fetch_ohlcv(SYMBOL, period="5d", interval="1h")
    bars = len(df)
    px = df["Close"].iloc[-1]
    show("bars", bars, "bougies 1h")
    show("dernier close", px, "USD")
except Exception as e:
    print(f"  {na('')} {C['red']}fetch_ohlcv failed: {e}{C['end']}")

# ── Binance ───────────────────────────────────────────────────
section("Binance")
try:
    t24 = ticker_24h(SYMBOL)
    if t24:
        show("ticker_24h — lastPrice", float(t24.get("lastPrice", 0)), "USD")
        show("ticker_24h — priceChangePercent", float(t24.get("priceChangePercent", 0)), "%")
except Exception as e:
    print(f"  {na('')} ticker_24h: {e}")
show("price (spot)", price(SYMBOL), "USD")
show("price_change_score", price_change_score(SYMBOL))
show("taker_pressure_score", taker_pressure_score(SYMBOL))
try:
    df_k = klines(SYMBOL, interval="1h", limit=24)
    if df_k is not None:
        show("klines 24h — barres", len(df_k), "bougies")
except Exception:
    pass

# ── CoinGecko ────────────────────────────────────────────────
section("CoinGecko")
show("price", cg_price(SYMBOL), "USD")
show("community_score", cg_community_score(SYMBOL))

# ── Fear & Greed ─────────────────────────────────────────────
section("Fear & Greed Index")
show("normalized_score", normalized_score())

# ── Binance Futures ──────────────────────────────────────────
section("Binance Futures")
show("long_short_ratio", long_short_ratio(SYMBOL))
show("funding_rate", funding_rate(SYMBOL))
show("top_trader_ratio", top_trader_ratio(SYMBOL))

# ── Reddit ───────────────────────────────────────────────────
section("Reddit (API publique)")
rc = RedditClient()
show("sentiment", rc.sentiment(SYMBOL, ["CryptoCurrency"], 25))

# ── Score composite (mode normal) ────────────────────────────
section("SCORE COMPOSITE — mode normal")
try:
    sc = StrategyConfig(threshold_long=0.0, threshold_short=-0.0, high_conviction=False)
    strat = MomentumSentimentStrategy(sc)
    sig = strat.evaluate(
        df if 'df' in dir() else None,
        reddit=rc.sentiment(SYMBOL, ["CryptoCurrency"], 25),
        futures_ls=long_short_ratio(SYMBOL),
        coingecko=cg_community_score(SYMBOL),
        fear_greed=normalized_score(),
        binance_change=price_change_score(SYMBOL),
        binance_taker=taker_pressure_score(SYMBOL),
    )
    score_color = C['green'] if sig.score > 0.1 else C['red'] if sig.score < -0.1 else C['yellow']
    sources = ", ".join(sig.active_sources) if sig.active_sources else "aucune"
    print(f"\n  {C['bold']}Score :{C['end']} {score_color}{sig.score:+.4f}{C['end']}")
    print(f"  {C['bold']}Décision :{C['end']} {C['green'] if sig.decision == 'LONG' else C['red'] if sig.decision == 'SHORT' else C['yellow']}{sig.decision}{C['end']}")
    print(f"  {C['dim']}Sources : {sources}{C['end']}")
except Exception as e:
    print(f"  {na('')} {C['red']}{e}{C['end']}")

# ── Score composite (high-conviction) ────────────────────────
section("SCORE COMPOSITE — high-conviction")
try:
    sc_hc = StrategyConfig(threshold_long=0.0, threshold_short=-0.0, high_conviction=True, min_active_sentiment_sources=4)
    strat_hc = MomentumSentimentStrategy(sc_hc)
    sig_hc = strat_hc.evaluate(
        df if 'df' in dir() else None,
        reddit=rc.sentiment(SYMBOL, ["CryptoCurrency"], 25),
        futures_ls=long_short_ratio(SYMBOL),
        coingecko=cg_community_score(SYMBOL),
        fear_greed=normalized_score(),
        binance_change=price_change_score(SYMBOL),
        binance_taker=taker_pressure_score(SYMBOL),
    )
    score_color = C['green'] if sig_hc.score > 0.1 else C['red'] if sig_hc.score < -0.1 else C['yellow']
    print(f"\n  {C['bold']}Score :{C['end']} {score_color}{sig_hc.score:+.4f}{C['end']}")
    print(f"  {C['bold']}Décision :{C['end']} {C['green'] if sig_hc.decision == 'LONG' else C['red'] if sig_hc.decision == 'SHORT' else C['yellow']}{sig_hc.decision}{C['end']}")
except Exception as e:
    print(f"  {na('')} {C['red']}{e}{C['end']}")

print(f"\n{C['cyan']}{'─' * 60}{C['end']}\n")