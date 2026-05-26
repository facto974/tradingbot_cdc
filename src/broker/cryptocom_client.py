"""Client Crypto.com Exchange REST v1 (sandbox UAT + live).

Doc officielle : https://exchange-docs.crypto.com/exchange/v1/rest-ws/index.html

Symboles : format utilisateur "BTC-USD" -> converti en "BTC_USD"
           (mapping USD->USD conserve selon preference utilisateur).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import httpx


LIVE = "https://api.crypto.com/exchange/v1"
SANDBOX = "https://uat-api.3ona.co/exchange/v1"

MAX_LEVEL = 3


class CryptoComAPIError(RuntimeError):
    def __init__(self, code, msg: str, url: str = ""):
        self.code = code
        self.msg = msg
        self.url = url
        super().__init__(f"Crypto.com API [{code}] on {url}: {msg}")


def _params_to_str(obj: Any, level: int = 0) -> str:
    """Serialisation deterministe des params pour la signature (spec officielle)."""
    if level >= MAX_LEVEL:
        return str(obj)

    if not isinstance(obj, dict):
        return str(obj)

    out = ""
    for key in sorted(obj.keys()):
        out += key
        v = obj[key]
        if v is None:
            out += "null"
        elif isinstance(v, bool):
            out += "true" if v else "false"
        elif isinstance(v, list):
            for sub in v:
                if isinstance(sub, dict):
                    out += _params_to_str(sub, level + 1)
                else:
                    out += str(sub)
        elif isinstance(v, dict):
            out += _params_to_str(v, level + 1)
        else:
            out += str(v)
    return out


class CryptoComClient:
    """Client Crypto.com Exchange - REST v1.

    Exemple :
        cc = CryptoComClient(api_key, api_secret, sandbox=True)
        print(cc.ticker("BTC-USD"))
        print(cc.balances())
    """

    def __init__(self, api_key: str = "", api_secret: str = "",
                 sandbox: bool = True, timeout: float = 15.0):
        self.api_key = api_key
        self.secret = api_secret.encode() if isinstance(api_secret, str) else api_secret
        self.base = SANDBOX if sandbox else LIVE
        self.timeout = timeout
        self._id = 0

    # ---------- Helpers ----------

    @staticmethod
    def _fmt(sym: str) -> str:
        """Convertit 'BTC-USD' -> 'BTC_USD'."""
        s = sym.replace("-", "_").upper()
        if s.endswith("_USDC"):
            s = s[:-5] + "_USD"
        return s

    @staticmethod
    def _nonce() -> int:
        return int(time.time() * 1000)

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def _sign(self, method: str, req_id: int, params: dict, nonce: int) -> str:
        param_str = _params_to_str(params or {})
        payload = f"{method}{req_id}{self.api_key}{param_str}{nonce}"
        return hmac.new(self.secret, payload.encode(), hashlib.sha256).hexdigest()

    def _check(self, data: dict, url: str) -> dict:
        code = data.get("code", 0)
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = -1
        if code_int != 0:
            raise CryptoComAPIError(
                code,
                str(data.get("message") or data.get("result") or data),
                url,
            )
        return data

    # ---------- Requete publique (GET) ----------

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{self.base}/{path}"
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(url, params=params or {})
            if r.status_code >= 400:
                raise CryptoComAPIError(r.status_code, r.text[:200], url)
            return self._check(r.json(), url)

    # ---------- Requete privee (POST signee) ----------

    def _post_private(self, method: str, params: dict | None = None) -> dict:
        if not self.api_key or not self.secret:
            raise CryptoComAPIError("AUTH", "api_key/api_secret manquants")

        req_id = self._next_id()
        nonce = self._nonce()
        params = params or {}
        sig = self._sign(method, req_id, params, nonce)

        body = {
            "id": req_id,
            "method": method,
            "api_key": self.api_key,
            "params": params,
            "nonce": nonce,
            "sig": sig,
        }
        url = f"{self.base}/{method}"
        headers = {"Content-Type": "application/json"}

        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(url, content=json.dumps(body), headers=headers)
            if r.status_code >= 400:
                raise CryptoComAPIError(r.status_code, r.text[:300], url)
            return self._check(r.json(), url)

    # ---------- Public ----------

    def get_instruments(self) -> list[dict]:
        d = self._get("public/get-instruments")
        return d.get("result", {}).get("data", [])

    def tickers(self) -> list[dict]:
        d = self._get("public/get-tickers")
        return d.get("result", {}).get("data", [])

    def ticker(self, symbol: str) -> dict:
        ccy = self._fmt(symbol)
        d = self._get("public/get-tickers", {"instrument_name": ccy})
        data = d.get("result", {}).get("data", [])
        return data[0] if data else {}

    def ohlcv(self, symbol: str, tf: str = "1h", limit: int = 100) -> list[dict]:
        ccy = self._fmt(symbol)
        d = self._get("public/get-candlestick", {
            "instrument_name": ccy,
            "timeframe": tf,
            "count": limit,
        })
        return d.get("result", {}).get("data", [])

    def order_book(self, symbol: str, depth: int = 50) -> dict:
        ccy = self._fmt(symbol)
        d = self._get("public/get-book", {"instrument_name": ccy, "depth": depth})
        data = d.get("result", {}).get("data", [])
        return data[0] if data else {}

    # ---------- Prive ----------

    def balances(self) -> list[dict]:
        d = self._post_private("private/user-balance")
        data = d.get("result", {}).get("data", [])
        if not data:
            return []
        positions = data[0].get("position_balances", [])
        return [{
            "currency": p.get("instrument_name") or p.get("currency"),
            "balance": float(p.get("quantity", 0)),
            "available": float(p.get("market_value", 0)),
        } for p in positions]

    def account_summary(self) -> dict:
        d = self._post_private("private/user-balance")
        data = d.get("result", {}).get("data", [])
        return data[0] if data else {}

    def place_order(self, symbol: str, side: str, qty: float,
                    price: float | None = None,
                    order_type: str = "LIMIT",
                    client_order_id: str | None = None) -> dict:
        ccy = self._fmt(symbol)
        params: dict[str, Any] = {
            "instrument_name": ccy,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": str(qty),
        }
        if price is not None and order_type.upper() != "MARKET":
            params["price"] = str(price)
        if client_order_id:
            params["client_oid"] = client_order_id

        d = self._post_private("private/create-order", params)
        return d.get("result", {})

    def cancel_order(self, order_id: str) -> dict:
        d = self._post_private("private/cancel-order", {"order_id": order_id})
        return d.get("result", {})

    def cancel_all(self, symbol: str | None = None) -> dict:
        params = {"instrument_name": self._fmt(symbol)} if symbol else {}
        d = self._post_private("private/cancel-all-orders", params)
        return d.get("result", {})

    def active_orders(self, symbol: str | None = None) -> list[dict]:
        params = {"instrument_name": self._fmt(symbol)} if symbol else {}
        d = self._post_private("private/get-open-orders", params)
        return d.get("result", {}).get("data", [])

    def order_status(self, order_id: str) -> dict:
        d = self._post_private("private/get-order-detail", {"order_id": order_id})
        return d.get("result", {})

    def trades(self, symbol: str | None = None, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if symbol:
            params["instrument_name"] = self._fmt(symbol)
        d = self._post_private("private/get-trades", params)
        return d.get("result", {}).get("data", [])


if __name__ == "__main__":
    cc = CryptoComClient(
        api_key="VOTRE_CLE_UAT",
        api_secret="VOTRE_SECRET_UAT",
        sandbox=True,
    )
    print("Ticker BTC-USD ->", cc.ticker("BTC-USD"))
    print("Balances       ->", cc.balances())