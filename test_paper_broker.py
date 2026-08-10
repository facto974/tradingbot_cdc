"""Tests du PaperBroker — vérifie la logique long/short, buy-to-cover, equity."""
import sys

sys.path.insert(0, ".")
from src.broker.paper_broker import PaperBroker

# Test 1 : Long simple
b = PaperBroker(initial_cash=1000, fee_bps=10)
b.market("BTC", "buy", 2, 100)  # buy 2 @ 100
eq, unreal = b.equity({"BTC": 110})
# cash = 1000 - 200 - 0.2 = 799.8, valeur position = 2*110 = 220
# eq = cash + valeur_position = 799.8 + 220 = 1019.8
assert abs(eq - (1000 - 200 - 0.2 + 2 * 110)) < 0.01, f"Long equity {eq}"
assert abs(unreal - 20) < 0.01, f"Long unreal {unreal}"
print("Test 1 Long : OK")

# Test 2 : Short simple
b = PaperBroker(initial_cash=1000, fee_bps=10)
b.market("BTC", "sell", 2, 100)  # short 2 @ 100
eq, unreal = b.equity({"BTC": 90})
# cash = 1000 + 200 - 0.2 = 1199.8, dette = 2*90=180
assert abs(eq - (1199.8 - 180)) < 0.01, f"Short equity {eq}"
assert abs(unreal - 20) < 0.01, f"Short unreal {unreal}"
print("Test 2 Short : OK")

# Test 3 : Short puis rachat (buy to cover) pur
b = PaperBroker(initial_cash=1000, fee_bps=10)
b.market("BTC", "sell", 10, 100)  # short 10 @ 100 → cash = 1000+1000-1 = 1999
tr = b.market("BTC", "buy", 10, 90)  # rachat 10 @ 90
assert not tr.get("rejected"), "Buy to cover devrait passer"
assert abs(b.cash - (1999 - 900 - 0.9)) < 0.01, f"Cash {b.cash}"
eq, _ = b.equity({"BTC": 90})
assert abs(eq - b.cash) < 0.01, "Plus de position apres rachat"
print("Test 3 Buy-to-cover pur : OK")

# Test 4 : Buy to cover insuffisant (cash negatif)
b = PaperBroker(initial_cash=100, fee_bps=10)
b.market("BTC", "sell", 10, 100)  # short 10 @ 100 → cash = 100+1000-1 = 1099
tr = b.market("BTC", "buy", 10, 200)  # rachat 10 @ 200 coute 2000+2 > 1099
assert tr.get("rejected"), "Devrait etre rejete (cash insuffisant)"
assert abs(b.cash - 1099) < 0.01, f"Cash doit rester intact {b.cash}"
assert len(b.trades) == 1, "Seul le short doit etre enregistre"
print("Test 4 Buy-to-cover rejete : OK")

# Test 5 : Short + extra long (buy to cover avec excedent)
b = PaperBroker(initial_cash=1000, fee_bps=10)
b.market("BTC", "sell", 10, 100)  # short 10 @ 100 → cash = 1999
tr = b.market("BTC", "buy", 15, 90)  # rachat 10 + long 5
assert not tr.get("rejected"), "Extra long devrait passer"
# cash = 1999 - 900 - 0.9 (close) - 450 - 0.45 (extra) = 647.65
assert abs(b.cash - 647.65) < 0.01, f"Cash {b.cash}"
assert b.positions["BTC"].qty == 5, "Position long 5"
assert b.positions["BTC"].side == "buy"
print("Test 5 Short + extra long : OK")

print()
print("TOUS LES TESTS PASSENT")