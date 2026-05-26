"""Test script for CryptoComClient – uses CryptoCom REST API correctly."""
import os
import sys
import json
import time
import base64
import hashlib
import hmac
import httpx
from dotenv import load_dotenv
load_dotenv()  

from src.broker.cryptocom_client import CryptoComClient

API_KEY    = os.getenv("CRYPTOCOM_API_KEY", "")
API_SECRET = os.getenv("CRYPTOCOM_API_SECRET", "")
SANDBOX    = os.getenv("CRYPTOCOM_SANDBOX", "true").lower() == "true"

if not API_KEY or not API_SECRET:
    print("⚠️  Aucune clé API trouvée dans l'environnement.")
    print("   Veuillez définir CRYPTOCOM_API_KEY et CRYPTOCOM_API_SECRET dans .env")
    sys.exit(1)

client   = CryptoComClient(api_key=API_KEY, api_secret=API_SECRET, sandbox=SANDBOX)
base_url = "https://api.sandbox.cryptocom.com" if SANDBOX else "https://api.cryptocom.com"

def _sign_request(path: str) -> dict:
    """Construit les headers HMAC pour un appel privé direct."""
    nonce = str(int(time.time() * 1000))
    body  = {"request": path, "nonce": nonce}
    b64 = base64.b64encode(json.dumps(body).encode())
    sig = hmac.new(
        API_SECRET.encode() if isinstance(API_SECRET, str) else API_SECRET,
        b64,
        hashlib.sha384,
    ).hexdigest()
    return {
        "Content-Type": "text/plain",
        "X-CRYPTOCOM-APIKEY": API_KEY,
        "X-CRYPTOCOM-PAYLOAD": b64.decode(),
        "X-CRYPTOCOM-SIGNATURE": sig,
    }

def test_public():
    """Endpoints publics – pas d'authentification."""
    print("\n── Endpoints publics ──")

    # Book : OK (sandbox + live)
    try:
        r = httpx.get(f"{base_url}/v1/book/BTCUSD")
        r.raise_for_status()
        d = r.json()
        print(f"✅ Book BTCUSD  : {len(d['bids'])} bids, {len(d['asks'])} asks")
    except Exception as e:
        print(f"❌ Book          : {e}")

    # Ticker : utiliser BTCUSD (sans tiret, majuscule) pour le live
    try:
        r = httpx.get(f"{base_url}/v1/pubticker/BTCUSD")
        r.raise_for_status()
        d = r.json()
        print(f"✅ Ticker public : ${d['last']}")
    except Exception as e:
        print(f"❌ Ticker public : {e}")

def test_private():
    """Endpoints privés nécessitant authentification HMAC."""
    print("\n── Endpoints privés ──")

    # Ticker via client (symbole avec tiret, converti par le client)
    try:
        t = client.ticker("BTC-USD")
        print(f"✅ Ticker privé  : ${t['last']}")
    except Exception as e:
        print(f"❌ Ticker privé  : {e}")

    # Balances – test direct pour meilleur diagnostic
    headers = _sign_request("/v1/balances")
    r = httpx.post(f"{base_url}/v1/balances", headers=headers)
    print(f"✅ Balances      : status={r.status_code}")
    if r.status_code == 200:
        data = r.json()
        for b in data:
            amt = float(b.get("available", b.get("amount", 0)))
            if amt > 0:
                print(f"   {b['currency']}: {amt}")
        if not data:
            print("   (compte vide)")
    elif r.status_code == 400 and "MissingAccounts" in r.text:
        print("   ⚠️  Sandbox ne supporte pas /v1/balances")
        print("   → C'est normal pour un sandbox, pas d'inquiétude")
    else:
        print(f"   {r.text[:200]}")

    # Ordre market – sandbox seulement
    if SANDBOX:
        print("\n── Ordre market (sandbox) ──")
        try:
            order = client.place_order(
                symbol="BTC-USD",
                side="buy",
                qty=0.00001,
                order_type="exchange market"
            )
            print(f"✅ Ordre market  : statut={order.get('executions', [])}")
        except Exception as e:
            print(f"❌ Ordre market  : {e}")
    else:
        print("\n⚠️  Ordre market ignoré en LIVE (évite un trade réel)")

def test_account():
    """Test /v1/account (endpoint plus fiable en sandbox que balances)."""
    print("\n── Compte (v1/account) ──")
    headers = _sign_request("/v1/account")
    r = httpx.post(f"{base_url}/v1/account", headers=headers)
    if r.status_code == 200:
        data = r.json()
        print(f"✅ Account       : {len(data.get('accounts', []))} comptes")
        for acc in data.get("accounts", []):
            amt = float(acc.get("availableForTrading", 0))
            if amt > 0:
                print(f"   {acc['currency']}: {amt}")
    else:
        print(f"❌ Account       : {r.status_code} {r.text[:200]}")

if __name__ == "__main__":
    mode = "SANDBOX" if SANDBOX else "LIVE"
    print("═" * 55)
    print(f"  Test CryptoComClient — {mode}")
    print(f"  URL  : {base_url}")
    print(f"  Key  : {API_KEY[:10]}...")
    print("═" * 55)

    test_public()
    test_private()
    test_account()