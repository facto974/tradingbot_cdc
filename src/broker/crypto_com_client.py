"""Client Crypto.com Exchange REST + WebSocket.
Symboles au format BTC-USDC → converti en BTC_USDC.

API v1 : GET https://api.crypto.com/exchange/v1/public/...
       : GET/POST https://api.crypto.com/exchange/v1/private/... (HMAC signé)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx


LIVE = "https://api.crypto.com/exchange"
SANDBOX = "https://uat-api.3ona.co"


class CryptoComAPIError(RuntimeError):
    def __init__(self, code: int | str, msg: str, url: str = ""):
        self.code = code; self.msg = msg; self.url = url
        super().__init__(f"Crypto.com API [{code}] on {url}: {msg}")


class CryptoComClient:
    """Client Crypto.com Exchange — REST v1.

    Utilisation :
        cc = CryptoComClient(api_key, secret, sandbox=True)
        t = cc.ticker("BTC-USDC")
        b = cc.balances()
    """

    def __init__(self, api_key: str = "", api_secret: str = "",
                 sandbox: bool = True, timeout: float = 15.0):
        self.api_key = api_key
        self.secret = api_secret.encode() if isinstance(api_secret, str) else api_secret
        self.base = SANDBOX if sandbox else LIVE
        self.timeout = timeout

    @staticmethod
    def _fmt(sym: str) -> str:
        """Convertit BTC-USDC → BTC_USD (Crypto.com utilise USD, pas USDC)."""
        s = sym.replace("-", "_").upper()
        # Crypto.com Exchange trade en USD pour les paires spot, pas USDC
        # On mappe -USDC → _USD car les paires USDC n'existent pas
        if s.endswith("_USDC"):
            s = s[:-5] + "_USD"
        return s

    @staticmethod
    def _ts() -> str:
        return str(int(time.time() * 1000))

    def _sign(self, nonce: str, method: str, path: str, body_str: str) -> str:
        """HMAC-SHA256 hex signature."""
        msg = nonce + method.upper() + path + body_str
        return hmac.new(self.secret, msg.encode(), hashlib.sha256).hexdigest()

    def _req(self, method: str, path: str, params: dict | None = None,
             json_body: dict | None = None, signed: bool = False) -> dict:
        url = f"{self.base}{path}"
        nonce = self._ts()
        headers = {}
        body_str = ""

        if json_body is not None:
            body_str = json.dumps(json_body)
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            # Crypto.com exige un body JSON même vide pour les POST privés
            body_str = "{}"
            headers["Content-Type"] = "application/json"

        if signed:
            sig = self._sign(nonce, method, path, body_str)
            headers.update({
                "Content-Type": "application/json",
                "X-MAL-API-KEY": self.api_key,
                "X-MAL-SIGNATURE": sig,
                "X-MAL-NONCE": nonce,
            })

        with httpx.Client(timeout=self.timeout) as c:
            if method == "GET":
                r = c.get(url, params=params, headers=headers)
            else:
                r = c.post(url, params=params, content=body_str, headers=headers)

            if r.status_code >= 400:
                raise CryptoComAPIError(r.status_code, r.text[:200], url)
            data = r.json()
            code = data.get("code")
            if code != 0:
                raise CryptoComAPIError(code, str(data.get("result", r.text[:200])), url)
            return data

    # ── Public ──────────────────────────────────────────────────────────

    def get_instruments(self) -> list[dict]:
        d = self._req("GET", "/v1/public/get-instruments")
        return d.get("result", {}).get("data", [])

    def ticker(self, symbol: str) -> dict:
        ccy = self._fmt(symbol)
        d = self._req("GET", "/v1/public/get-tickers")
        for t in d.get("result", {}).get("data", []):
            if t.get("i", "").upper() == ccy:
                return t
        return {}

    def ohlcv(self, symbol: str, tf: str = "1h", limit: int = 100) -> list[list]:
        ccy = self._fmt(symbol)
        d = self._req("GET", "/v1/public/get-candlestick",
                       {"instrument_name": ccy, "timeframe": tf, "limit": limit})
        return d.get("result", {}).get("data", [])

    # ── Privé ───────────────────────────────────────────────────────────

    def balances(self) -> list[dict]:
        d = self._req("POST", "/v1/private/get-account-summary", json_body={}, signed=True)
        accounts = d.get("result", {}).get("accounts", [])
        return [{"currency": a.get("currency"),
                  "balance": float(a.get("balance", 0)),
                  "available": float(a.get("available", 0))}
                for a in accounts]

    def place_order(self, symbol: str, side: str, qty: float,
                    price: float | None = None,
                    order_type: str = "LIMIT",
                    client_order_id: str | None = None) -> dict:
        ccy = self._fmt(symbol)
        body = {"instrument_name": ccy, "side": side.upper(),
                "type": order_type, "quantity": str(qty)}
        if price is not None:
            body["price"] = str(price)
        if client_order_id:
            body["client_oid"] = client_order_id
        d = self._req("POST", "/v1/private/create-order",
                      json_body=body, signed=True)
        return d.get("result", {})

    def cancel_order(self, order_id: str) -> dict:
        d = self._req("POST", "/v1/private/cancel-order",
                      json_body={"order_id": order_id}, signed=True)
        return d.get("result", {})

    def active_orders(self) -> list[dict]:
        d = self._req("POST", "/v1/private/get-open-orders",
                      json_body={}, signed=True)
        return d.get("result", {}).get("data", [])