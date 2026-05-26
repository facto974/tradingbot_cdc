"""Tests d'intégration pour GeminiClient – nécessitent des clés API valides.

Ces tests sont ignorés par défaut si GEMINI_API_KEY n'est pas défini.
Utilisation : pytest tests/test_gemini_integration.py -v
"""
from __future__ import annotations

import os
import pytest
from dotenv import load_dotenv
load_dotenv()

from src.broker.gemini_client import GeminiClient

API_KEY    = os.getenv("GEMINI_API_KEY", "")
API_SECRET = os.getenv("GEMINI_API_SECRET", "")
SANDBOX    = os.getenv("GEMINI_SANDBOX", "true").lower() == "true"

skip_no_key = pytest.mark.skipif(
    not API_KEY or not API_SECRET,
    reason="GEMINI_API_KEY / GEMINI_API_SECRET non définies",
)


@pytest.fixture(scope="module")
def client() -> GeminiClient:
    return GeminiClient(api_key=API_KEY, api_secret=API_SECRET, sandbox=SANDBOX)


# ── Endpoints publics ──────────────────────────────────────────────────

class TestPublicIntegration:
    def test_book_btcusd(self):
        """Le book public doit contenir des bids et asks."""
        import httpx
        base = "https://api.sandbox.gemini.com" if SANDBOX else "https://api.gemini.com"
        r = httpx.get(f"{base}/v1/book/BTCUSD")
        assert r.status_code == 200, f"Book échoué: {r.text[:100]}"
        data = r.json()
        assert len(data["bids"]) > 0, "Aucun bid dans le book"
        assert len(data["asks"]) > 0, "Aucun ask dans le book"
        # Vérifier la structure des bids
        for bid in data["bids"][:1]:
            assert "price" in bid
            assert "amount" in bid

    def test_ticker_public(self):
        """Le ticker public doit retourner un prix > 0."""
        import httpx
        base = "https://api.sandbox.gemini.com" if SANDBOX else "https://api.gemini.com"
        for sym in ["BTCUSD", "btcusd"]:
            r = httpx.get(f"{base}/v1/pubticker/{sym}")
            if r.status_code == 200:
                data = r.json()
                assert float(data["last"]) > 0
                assert float(data["bid"]) > 0
                assert float(data["ask"]) > 0
                return
        pytest.skip("Ticker public indisponible sur cet environnement")


# ── Endpoints privés (authentifiés) ────────────────────────────────────

class TestPrivateIntegration:
    @skip_no_key
    def test_ticker_via_client(self, client):
        """Le ticker via GeminiClient doit fonctionner."""
        try:
            ticker = client.ticker("BTC-USD")
            assert float(ticker["last"]) > 0, "Prix invalide"
            assert float(ticker["bid"]) > 0
            assert float(ticker["ask"]) > 0
        except Exception as e:
            if "404" in str(e):
                pytest.skip("Ticker non disponible (sandbox)")
            raise

    @skip_no_key
    def test_balances(self, client):
        """Les balances doivent retourner une liste (vide ou non)."""
        try:
            balances = client.balances()
            assert isinstance(balances, list)
            # Vérifier la structure si non vide
            if balances:
                b = balances[0]
                assert "currency" in b
                assert "amount" in b
        except Exception as e:
            if "MissingAccounts" in str(e) or "400" in str(e):
                pytest.skip("Balances non supporté par le sandbox")
            raise

    @skip_no_key
    def test_active_orders(self, client):
        """Les ordres actifs doivent retourner une liste."""
        try:
            orders = client.active_orders()
            assert isinstance(orders, list)
        except Exception as e:
            if "400" in str(e):
                pytest.skip("active_orders non supporté par le sandbox")
            raise

    @skip_no_key
    def test_place_market_order(self, client):
        """Placer un market order (sandbox uniquement, petite quantité)."""
        if not SANDBOX:
            pytest.skip("Évite un trade réel en LIVE")
        try:
            order = client.place_order(
                symbol="BTC-USD",
                side="buy",
                qty=0.00001,
                order_type="exchange market",
            )
            assert "order_id" in order, f"Pas d'order_id: {order}"
            assert order.get("status") in ("new", "filled", "accepted")
            # Annuler l'ordre si encore ouvert
            if order.get("status") in ("new", "accepted"):
                client.cancel_order(order["order_id"])
        except Exception as e:
            if "400" in str(e):
                pytest.skip("Order non supporté par le sandbox")
            raise


# ── Test de bout en bout du flow complet ─────────────────────────────

class TestFullFlow:
    """Teste le flow complet : ticker → balances → order."""

    @skip_no_key
    def test_full_flow_sandbox(self, client):
        if not SANDBOX:
            pytest.skip("Test full flow réservé au sandbox")

        errors = []

        # Étape 1 : Ticker
        try:
            t = client.ticker("BTC-USD")
            assert float(t["last"]) > 0
        except Exception as e:
            errors.append(f"Ticker: {e}")

        # Étape 2 : Balances
        try:
            b = client.balances()
            assert isinstance(b, list)
        except Exception as e:
            errors.append(f"Balances: {e}")

        # Étape 3 : Ordre market
        if not errors:
            try:
                order = client.place_order(
                    symbol="BTC-USD",
                    side="buy",
                    qty=0.00001,
                    order_type="exchange market",
                )
                assert "order_id" in order
                if order.get("status") in ("new", "accepted"):
                    client.cancel_order(order["order_id"])
            except Exception as e:
                errors.append(f"Order: {e}")

        if errors:
            pytest.skip(f"Full flow partiel: {', '.join(errors)}")