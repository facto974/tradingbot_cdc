"""Client Gemini Exchange REST — sandbox + live.

Doc API : https://docs.gemini.com/rest-api/
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

import httpx


SANDBOX_URL = "https://api.sandbox.gemini.com"
LIVE_URL = "https://api.gemini.com"


class GeminiAPIError(RuntimeError):
    """Erreur enrichie avec le status HTTP + le body JSON renvoyé par Gemini.

    Permet de récupérer `reason` / `errorId` côté appelant pour diagnostiquer
    finement (InvalidSignature, InvalidNonce, EndpointMismatch, MissingRole...).
    """

    def __init__(self, status: int, body: str, url: str = ""):
        self.status = status
        self.body = body
        self.url = url
        # Tente d'extraire le reason JSON pour un message lisible
        reason = body
        try:
            j = json.loads(body)
            if isinstance(j, dict):
                reason = j.get("reason") or j.get("message") or body
        except (json.JSONDecodeError, TypeError):
            pass
        super().__init__(f"Gemini API {status} on {url}: {reason}")


class GeminiClient:
    def __init__(self, api_key: str, api_secret: str, sandbox: bool = True,
                 timeout: float = 10.0):
        self.api_key = api_key
        self.api_secret = api_secret.encode() if isinstance(api_secret, str) else api_secret
        self.base = SANDBOX_URL if sandbox else LIVE_URL
        self.timeout = timeout

    # ---- public ----
    def ticker(self, symbol: str) -> dict:
        sym = symbol.replace("-", "").lower()  # BTC-USD -> btcusd
        with httpx.Client(timeout=self.timeout) as c:
            r = c.get(f"{self.base}/v1/pubticker/{sym}")
            r.raise_for_status()
            return r.json()

    # ---- private ----
    def _private(self, path: str, payload: dict[str, Any]) -> dict:
        if not self.api_key or not self.api_secret:
            raise RuntimeError("Gemini API key/secret not configured")
        nonce = str(int(time.time() * 1000))
        # Master API keys: use 'account' as a string (the nickname of the account)
        body = {**payload, "request": path, "nonce": nonce}
        if self.api_key.startswith("master-"):
            body["account"] = "primary"
        b64 = base64.b64encode(json.dumps(body).encode())
        sig = hmac.new(self.api_secret, b64, hashlib.sha384).hexdigest()
        headers = {
            "Content-Type": "text/plain",
            "X-GEMINI-APIKEY": self.api_key,
            "X-GEMINI-PAYLOAD": b64.decode(),
            "X-GEMINI-SIGNATURE": sig,
            "Cache-Control": "no-cache",
        }
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(f"{self.base}{path}", headers=headers)
            r.raise_for_status()
            return r.json()

    def balances(self) -> list[dict]:
        # Master API keys require the "accounts" parameter
        return self._private("/v1/balances", {"accounts": ["primary"]})

    def place_order(self, symbol: str, side: str, qty: float, price: float | None = None,
                    order_type: str = "exchange limit",
                    client_order_id: str | None = None) -> dict:
        """Place un ordre sur Gemini.

        Types supportés (Gemini doc) :
          - "exchange limit"    → ordre limit standard
          - "exchange stop limit" → ordre stop-limit
          - "auction-only"      → ordre à l'enchère
          - "indication-of-interest" → indication d'intérêt
        Pour un market order, utiliser "exchange limit" avec un price = le ticker actuel.
        Doc : https://docs.gemini.com/rest-api/#new-order
        """
        sym = symbol.replace("-", "").lower()
        payload: dict[str, str] = {
            "symbol": sym,
            "amount": str(qty),
            "side": side.lower(),
            "type": order_type,
        }
        if price is not None:
            payload["price"] = str(price)
        if client_order_id:
            payload["client_order_id"] = client_order_id
        return self._private("/v1/order/new", payload)

    def cancel_order(self, order_id: str) -> dict:
        return self._private("/v1/order/cancel", {"order_id": order_id})

    def active_orders(self) -> list[dict]:
        return self._private("/v1/orders", {})