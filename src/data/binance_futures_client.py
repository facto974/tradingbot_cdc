"""Binance Futures — Long/Short ratio, Funding Rate.
 
 Fournit des signaux de sentiment marché normalisés [-1, +1] via les endpoints
 publics Binance Futures (sans clé API).
 """
from __future__ import annotations
from typing import Optional
import logging

from ._http import get_json, to_binance_symbol

logger = logging.getLogger(__name__)

FUTURES_BASE = "https://fapi.binance.com"


def long_short_ratio(symbol: str, period: str = "1h", limit: int = 1) -> Optional[float]:
    """Ratio long/short global (Binance Futures) — normalisé [-1, +1].
    
    Utilise globalLongShortAccountRatio : proportion des comptes longs vs shorts.
    Retourne (long - short) / (long + short) ∈ [-1, +1].
    Retourne None si indisponible.
    """
    sym = to_binance_symbol(symbol)
    url = f"{FUTURES_BASE}/futures/data/globalLongShortAccountRatio"
    params = {"symbol": sym, "period": period, "limit": limit}
    try:
        data = get_json(url, params=params)
        if not isinstance(data, list) or len(data) == 0:
            return None
        item = data[-1]
        long_ratio = float(item["longAccount"])
        short_ratio = float(item["shortAccount"])
        total = long_ratio + short_ratio
        if total > 0:
            return (long_ratio - short_ratio) / total  # [-1, +1]
        return 0.0
    except Exception as e:
        logger.warning(f"Binance Futures long_short_ratio échoué pour {symbol}: {e}")
        return None


def funding_rate(symbol: str) -> Optional[float]:
    """Funding rate actuel — signe du sentiment marché.
    
    Funding rate > 0 → bullish (les longs paient), < 0 → bearish.
    Normalisé : clamp [-0.001, +0.001] à [-1, +1].
    """
    sym = to_binance_symbol(symbol)
    url = f"{FUTURES_BASE}/fapi/v1/premiumIndex"
    params = {"symbol": sym}
    try:
        data = get_json(url, params=params)
        if not isinstance(data, dict):
            return None
        fr = float(data.get("lastFundingRate", 0))
        return max(-1.0, min(1.0, fr * 1000))
    except Exception as e:
        logger.warning(f"Binance Futures funding_rate échoué pour {symbol}: {e}")
        return None


def top_trader_ratio(symbol: str, period: str = "1h", limit: int = 1) -> Optional[float]:
    """Ratio long/short des top traders — plus fiable que le ratio global.
    
    Pondéré par le volume des traders, pas seulement le nombre de comptes.
    """
    sym = to_binance_symbol(symbol)
    url = f"{FUTURES_BASE}/futures/data/topLongShortAccountRatio"
    params = {"symbol": sym, "period": period, "limit": limit}
    try:
        data = get_json(url, params=params)
        if not isinstance(data, list) or len(data) == 0:
            return None
        item = data[-1]
        long_ratio = float(item["longAccount"])
        short_ratio = float(item["shortAccount"])
        total = long_ratio + short_ratio
        if total > 0:
            return (long_ratio - short_ratio) / total
        return 0.0
    except Exception as e:
        logger.warning(f"Binance Futures top_trader_ratio échoué pour {symbol}: {e}")
        return None