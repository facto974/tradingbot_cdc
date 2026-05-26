"""Tests unitaires pour GeminiClient – toutes les méthodes sont mockées pour éviter les appels réels."""
from __future__ import annotations

import json
import time
import base64
import hashlib
import hmac
from unittest.mock import MagicMock, patch, ANY

import httpx
import pytest

from src.broker.gemini_client import GeminiClient, GeminiAPIError, SANDBOX_URL, LIVE_URL


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def mock_httpx(monkeypatch):
    """Remplace httpx.Client par un mock pour éviter les appels réseau."""
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"last": "80000.00", "bid": "79900", "ask": "80100"}
    mock_response.raise_for_status.return_value = None

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = mock_response
    mock_client.post.return_value = mock_response

    monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client)
    return mock_client


@pytest.fixture
def client(mock_httpx) -> GeminiClient:
    """Client Gemini en mode sandbox pour les tests."""
    return GeminiClient(
        api_key="test_key",
        api_secret="test_secret",
        sandbox=True,
    )


# ── Tests d'initialisation ────────────────────────────────────────────────

class TestInit:
    def test_sandbox_url(self):
        c = GeminiClient("key", "secret", sandbox=True)
        assert c.base == SANDBOX_URL

    def test_live_url(self):
        c = GeminiClient("key", "secret", sandbox=False)
        assert c.base == LIVE_URL

    def test_secret_encoded(self):
        c = GeminiClient("key", "secret", sandbox=True)
        assert isinstance(c.api_secret, bytes)

    def test_timeout_default(self):
        c = GeminiClient("key", "secret", sandbox=True)
        assert c.timeout == 10.0

    def test_timeout_custom(self):
        c = GeminiClient("key", "secret", sandbox=True, timeout=30.0)
        assert c.timeout == 30.0


# ── Tests du ticker public ───────────────────────────────────────────────

class TestTicker:
    def test_ticker_success(self, client, mock_httpx):
        result = client.ticker("BTC-USD")
        assert result["last"] == "80000.00"
        mock_httpx.get.assert_called_once_with(
            f"{SANDBOX_URL}/v1/pubticker/btcusd"
        )

    def test_ticker_converts_symbol(self, client, mock_httpx):
        client.ticker("ETH-USD")
        called_url = mock_httpx.get.call_args[0][0]
        assert "ethusd" in called_url

    def test_ticker_raises_on_http_error(self, client, mock_httpx):
        mock_httpx.get.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        with pytest.raises(httpx.HTTPStatusError):
            client.ticker("BTC-USD")


# ── Tests de la méthode _private ─────────────────────────────────────────

class TestPrivate:
    def test_raises_without_credentials(self):
        c = GeminiClient("", "", sandbox=True)
        with pytest.raises(RuntimeError, match="not configured"):
            c._private("/test", {})

    def test_headers_contain_api_key(self, client, mock_httpx):
        client._private("/v1/test", {"foo": "bar"})
        call_kwargs = mock_httpx.post.call_args[1]
        headers = call_kwargs["headers"]
        assert headers["X-GEMINI-APIKEY"] == "test_key"

    def test_headers_contain_payload(self, client, mock_httpx):
        client._private("/v1/test", {"foo": "bar"})
        call_kwargs = mock_httpx.post.call_args[1]
        headers = call_kwargs["headers"]

        # Décoder le payload et vérifier son contenu
        payload_b64 = headers["X-GEMINI-PAYLOAD"]
        decoded = json.loads(base64.b64decode(payload_b64).decode())
        assert decoded["request"] == "/v1/test"
        assert decoded["foo"] == "bar"
        assert "nonce" in decoded

    def test_headers_contain_signature(self, client, mock_httpx):
        client._private("/v1/test", {})
        call_kwargs = mock_httpx.post.call_args[1]
        headers = call_kwargs["headers"]
        assert "X-GEMINI-SIGNATURE" in headers
        # La signature doit être un hex de 96 caractères (SHA384)
        assert len(headers["X-GEMINI-SIGNATURE"]) == 96

    def test_headers_content_type(self, client, mock_httpx):
        client._private("/v1/test", {})
        call_kwargs = mock_httpx.post.call_args[1]
        assert call_kwargs["headers"]["Content-Type"] == "text/plain"

    def test_calls_correct_url(self, client, mock_httpx):
        client._private("/v1/balances", {})
        call_args = mock_httpx.post.call_args[0]
        assert call_args[0] == f"{SANDBOX_URL}/v1/balances"

    def test_signature_validates(self, client, mock_httpx):
        """Vérifie que la signature HMAC est correctement calculée."""
        client._private("/v1/test", {"key": "value"})
        call_kwargs = mock_httpx.post.call_args[1]
        headers = call_kwargs["headers"]

        # Recalculer la signature attendue
        payload_b64 = headers["X-GEMINI-PAYLOAD"]
        expected_sig = hmac.new(
            b"test_secret",
            payload_b64.encode(),
            hashlib.sha384,
        ).hexdigest()
        assert headers["X-GEMINI-SIGNATURE"] == expected_sig

    def test_nonce_increases(self, client, mock_httpx):
        """Les nonces doivent être croissants (test avec 2 appels successifs)."""
        client._private("/v1/test1", {})
        call1_headers = mock_httpx.post.call_args[1]["headers"]

        client._private("/v1/test2", {})
        call2_headers = mock_httpx.post.call_args[1]["headers"]

        payload1 = json.loads(base64.b64decode(call1_headers["X-GEMINI-PAYLOAD"]).decode())
        payload2 = json.loads(base64.b64decode(call2_headers["X-GEMINI-PAYLOAD"]).decode())

        assert int(payload2["nonce"]) > int(payload1["nonce"])

    def test_raises_on_http_error(self, client, mock_httpx):
        error_response = MagicMock(spec=httpx.Response)
        error_response.status_code = 400
        error_response.text = '{"result":"error","reason":"InvalidNonce"}'
        mock_httpx.post.side_effect = httpx.HTTPStatusError(
            "400", request=MagicMock(), response=error_response
        )
        with pytest.raises(httpx.HTTPStatusError):
            client._private("/v1/test", {})


# ── Tests de balances ────────────────────────────────────────────────────

class TestBalances:
    def test_balances_calls_private(self, client, mock_httpx):
        mock_httpx.post.return_value.json.return_value = [
            {"currency": "BTC", "amount": "0.5", "available": "0.5"}
        ]
        result = client.balances()
        assert result[0]["currency"] == "BTC"
        assert float(result[0]["amount"]) == 0.5

    def test_balances_empty(self, client, mock_httpx):
        mock_httpx.post.return_value.json.return_value = []
        result = client.balances()
        assert result == []


# ── Tests de place_order ─────────────────────────────────────────────────

class TestPlaceOrder:
    def test_limit_order(self, client, mock_httpx):
        client.place_order(
            symbol="BTC-USD",
            side="buy",
            qty=0.01,
            price=80000.0,
            order_type="exchange limit",
        )
        call_kwargs = mock_httpx.post.call_args[1]
        payload_b64 = call_kwargs["headers"]["X-GEMINI-PAYLOAD"]
        payload = json.loads(base64.b64decode(payload_b64).decode())
        assert payload["symbol"] == "btcusd"
        assert payload["side"] == "buy"
        assert payload["amount"] == "0.01"
        assert payload["price"] == "80000.0"
        assert payload["type"] == "exchange limit"

    def test_market_order_without_price(self, client, mock_httpx):
        client.place_order(
            symbol="BTC-USD",
            side="sell",
            qty=0.5,
            order_type="exchange market",
        )
        call_kwargs = mock_httpx.post.call_args[1]
        payload_b64 = call_kwargs["headers"]["X-GEMINI-PAYLOAD"]
        payload = json.loads(base64.b64decode(payload_b64).decode())
        assert payload["symbol"] == "btcusd"
        assert payload["type"] == "exchange market"
        assert "price" not in payload

    def test_market_order_with_price_ignores_price(self, client, mock_httpx):
        """Pour un market order, le prix ne doit pas être envoyé même s'il est fourni."""
        client.place_order(
            symbol="BTC-USD",
            side="buy",
            qty=0.01,
            price=99999.0,
            order_type="exchange market",
        )
        call_kwargs = mock_httpx.post.call_args[1]
        payload_b64 = call_kwargs["headers"]["X-GEMINI-PAYLOAD"]
        payload = json.loads(base64.b64decode(payload_b64).decode())
        assert "price" not in payload

    def test_with_client_order_id(self, client, mock_httpx):
        client.place_order(
            symbol="BTC-USD",
            side="buy",
            qty=0.01,
            order_type="exchange limit",
            price=80000.0,
            client_order_id="my-custom-id",
        )
        call_kwargs = mock_httpx.post.call_args[1]
        payload_b64 = call_kwargs["headers"]["X-GEMINI-PAYLOAD"]
        payload = json.loads(base64.b64decode(payload_b64).decode())
        assert payload["client_order_id"] == "my-custom-id"

    def test_symbol_conversion(self, client, mock_httpx):
        """Vérifie que ETH-USD est converti en ethusd."""
        client.place_order("ETH-USD", "buy", 1.0, order_type="exchange market")
        call_kwargs = mock_httpx.post.call_args[1]
        payload_b64 = call_kwargs["headers"]["X-GEMINI-PAYLOAD"]
        payload = json.loads(base64.b64decode(payload_b64).decode())
        assert payload["symbol"] == "ethusd"

    def test_live_url_for_live_mode(self, mock_httpx):
        """En mode live, l'URL doit être api.gemini.com."""
        c = GeminiClient("key", "secret", sandbox=False)
        c.place_order("BTC-USD", "buy", 0.01, order_type="exchange market")
        call_args = mock_httpx.post.call_args[0]
        assert LIVE_URL in call_args[0]
        assert "sandbox" not in call_args[0]


# ── Tests de cancel_order ─────────────────────────────────────────────────

class TestCancelOrder:
    def test_cancel_order_sends_order_id(self, client, mock_httpx):
        client.cancel_order("order-123")
        call_kwargs = mock_httpx.post.call_args[1]
        payload_b64 = call_kwargs["headers"]["X-GEMINI-PAYLOAD"]
        payload = json.loads(base64.b64decode(payload_b64).decode())
        assert payload["order_id"] == "order-123"
        assert payload["request"] == "/v1/order/cancel"


# ── Tests de active_orders ───────────────────────────────────────────────

class TestActiveOrders:
    def test_active_returns_list(self, client, mock_httpx):
        mock_httpx.post.return_value.json.return_value = [
            {"order_id": "1", "symbol": "btcusd", "side": "buy"}
        ]
        result = client.active_orders()
        assert isinstance(result, list)
        assert result[0]["order_id"] == "1"

    def test_active_orders_correct_path(self, client, mock_httpx):
        client.active_orders()
        call_args = mock_httpx.post.call_args[0]
        assert call_args[0] == f"{SANDBOX_URL}/v1/orders"


# ── Tests de GeminiAPIError ──────────────────────────────────────────────

class TestGeminiAPIError:
    def test_error_message_with_reason(self):
        err = GeminiAPIError(
            status=400,
            body='{"result":"error","reason":"InvalidNonce"}',
            url="/v1/test",
        )
        assert "InvalidNonce" in str(err)
        assert "400" in str(err)

    def test_error_message_without_reason(self):
        err = GeminiAPIError(status=500, body="Internal Server Error", url="/v1/test")
        assert "Internal Server Error" in str(err)
        assert "500" in str(err)

    def test_error_message_with_unknown_body(self):
        err = GeminiAPIError(status=400, body="not json")
        assert "400" in str(err)
        assert "not json" in str(err)

    def test_attributes(self):
        err = GeminiAPIError(status=403, body='{"reason":"Forbidden"}', url="/v1/test")
        assert err.status == 403
        assert err.body == '{"reason":"Forbidden"}'
        assert err.url == "/v1/test"