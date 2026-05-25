"""Helpers réseau partagés par les data clients."""
from __future__ import annotations

import time
from typing import Any

import httpx

DEFAULT_TIMEOUT = 10.0
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_json(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    retries: int = 2,
) -> Any:
    """GET JSON avec retry exponentiel + User-Agent navigateur.

    Lève RuntimeError après tous les retries. Les clients data doivent
    encapsuler dans try/except pour retourner None ("source indisponible").
    """
    merged_headers = {**DEFAULT_HEADERS, **(headers or {})}
    last: Exception | None = None

    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                r = client.get(url, params=params, headers=merged_headers)
                r.raise_for_status()
                return r.json()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.5 * (attempt + 1))

    raise RuntimeError(f"get_json failed: {url}: {last}")


def to_finnhub_symbol(sym: str) -> str:
    """BTC-USD -> BINANCE:BTCUSDT (fallback)."""
    base = sym.split("-")[0]
    return f"BINANCE:{base}USDT"


def to_coingecko_id(sym: str) -> str:
    """BTC-USD -> 'bitcoin'."""
    base = sym.split("-")[0].lower()
    table = {
        "btc":       "bitcoin",
        "eth":       "ethereum",
        "sol":       "solana",
        "doge":      "dogecoin",
        "ada":       "cardano",
        "avax":      "avalanche-2",
        "matic":     "matic-network",
        "pol":       "matic-network",  # POL = nouveau nom MATIC
        "dot":       "polkadot",
        "ltc":       "litecoin",
        "link":      "chainlink",
        "xrp":       "ripple",
        "bnb":       "binancecoin",
        "trx":       "tron",
        "atom":      "cosmos",
        "near":      "near",
        "arb":       "arbitrum",
        "op":        "optimism",
        "shib":      "shiba-inu",
        "uni":       "uniswap",
        "aave":      "aave",
        "algo":      "algorand",
        "amp":       "amp-token",
        "ankr":      "ankr",
        "ape":       "apecoin",
        "api3":      "api3",
        "bch":       "bitcoin-cash",
        "bat":       "basic-attention-token",
        "bome":      "book-of-memes",
        "bonk":      "bonk",
        "comp":      "compound-governance-token",
        "crv":       "curve-dao-token",
        "ctx":       "cryptex-finance",
        "cube":      "somnium-space-cubes",
        "drift":     "drift-protocol",
        "elon":      "dogelon-mars",
        "eul":       "euler",
        "fet":       "fetch-ai",
        "fil":       "filecoin",
        "floki":     "floki",
        "ftm":       "fantom",
        "gala":      "gala",
        "gmt":       "stepn",
        "grt":       "the-graph",
        "hnt":       "helium",
        "hype":      "hyperliquid",
        "imx":       "immutable-x",
        "inj":       "injective",
        "iotx":      "iotex",
        "jitosol":   "jito-staked-sol",
        "jto":       "jito",
        "jup":       "jupiter-exchange-solana",
        "kmno":      "kamino",
        "ldo":       "lido-dao",
        "lpt":       "livepeer",
        "lrc":       "loopring",
        "mana":      "decentraland",
        "mask":      "mask-network",
        "mew":       "cat-in-a-dogs-world",
        "mon":       "mon-protocol",
        "pengu":     "pudgy-penguins",
        "pepe":      "pepe",
        "pnut":      "peanut-the-squirrel",
        "popcat":    "popcat",
        "pump":      "pump-it-up",
        "pyth":      "pyth-network",
        "qnt":       "quant-network",
        "rndr":      "render-token",
        "samo":      "samoyedcoin",
        "sand":      "the-sandbox",
        "skl":       "skale",
        "sky":       "sky",
        "storj":     "storj",
        "sui":       "sui",
        "sushi":     "sushi",
        "ton":       "the-open-network",
        "uma":       "uma",
        "wct":       "worldcoin-wct",
        "wif":       "dogwifcoin",
        "wld":       "worldcoin-wld",
        "wlfi":      "worldliberty-financial",
        "xtz":       "tezos",
        "yfi":       "yearn-finance",
        "zec":       "zcash",
    }
    return table.get(base, base)


def to_coin_full_name(sym: str) -> str:
    """BTC-USD -> 'Bitcoin' (utile pour la recherche Reddit)."""
    base = sym.split("-")[0].lower()
    table = {
        "btc":   "Bitcoin",
        "eth":   "Ethereum",
        "sol":   "Solana",
        "doge":  "Dogecoin",
        "ada":   "Cardano",
        "avax":  "Avalanche",
        "matic": "Polygon",
        "dot":   "Polkadot",
        "ltc":   "Litecoin",
        "link":  "Chainlink",
        "xrp":   "Ripple",
        "bnb":   "Binance",
        "trx":   "Tron",
        "atom":  "Cosmos",
        "near":  "NEAR",
        "arb":   "Arbitrum",
        "op":    "Optimism",
        "shib":  "Shiba",
        "uni":   "Uniswap",
    }
    return table.get(base, base.upper())


def to_binance_symbol(sym: str) -> str:
    """BTC-USD -> BTCUSDT (Binance public API)."""
    if "-" in sym:
        base, quote = sym.split("-", 1)
    else:
        base, quote = sym, "USD"

    if quote.upper() == "USD":
        quote = "USDT"

    return f"{base.upper()}{quote.upper()}"