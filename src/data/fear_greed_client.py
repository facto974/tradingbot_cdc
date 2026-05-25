"""Fear & Greed Index — alternative.me (gratuit, sans clé)."""
from __future__ import annotations

from ._http import get_json

URL = "https://api.alternative.me/fng/"


def current_index() -> dict | None:
    """Retourne {'value': int, 'classification': str} ou None si indisponible."""
    try:
        data = get_json(URL, params={"limit": 1, "format": "json"})
        item = data["data"][0]
        return {
            "value":          int(item["value"]),
            "classification": item["value_classification"],
        }
    except Exception:
        return None


def normalized_score() -> float | None:
    """Centre [0, 100] sur [-1, +1].

    -1 = peur extrême  |  0 = neutre  |  +1 = avidité extrême
    Retourne None si l'API est indisponible.
    """
    idx = current_index()
    if idx is None:
        return None
    return (idx["value"] - 50.0) / 50.0