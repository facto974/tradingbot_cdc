"""Paper broker — simule l'exécution en local pour tester sans clé CryptoCom."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    symbol: str = ""
    qty: float = 0.0      # positif = long, négatif = short
    avg_price: float = 0.0
    side: str = ""        # "buy" = long, "sell" = short, "" = pas de position


class PaperBroker:
    def __init__(self, initial_cash: float = 10_000.0, fee_bps: float = 10):
        self.cash = initial_cash
        self.fee_bps = fee_bps
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.trades: list[dict] = []

    def _fee(self, notional: float) -> float:
        return notional * self.fee_bps / 1e4

    def _can_afford(self, side: str, notional: float, fee: float, qty: float, price: float) -> bool:
        """Vérifie si le cash disponible suffit pour un ordre LONG."""
        if side.lower() != "buy":
            return True  # un sell (short ou close) apporte du cash, pas besoin de vérifier
        # Si on a déjà une position short, on utilise le close pour financer l'extra long
        return notional + fee <= self.cash

    def market(self, symbol: str, side: str, qty: float, price: float) -> dict:
        notional = qty * price
        fee = self._fee(notional)
        pos = self.positions.setdefault(symbol, Position(symbol=symbol))
        pnl = 0.0

        if side.lower() == "buy":
            if pos.qty >= 0 and not self._can_afford(side, notional, fee, qty, price):
                return {"symbol": symbol, "side": side, "qty": qty, "price": price,
                        "fee": 0.0, "pnl": 0.0, "rejected": True, "reason": "insufficient_cash"}
            if pos.qty < 0:
                # Fermer/réduire SHORT : on rachète (buy to cover)
                close_qty = min(qty, -pos.qty)
                close_notional = close_qty * price
                close_fee = self._fee(close_notional)
                # Partie excédentaire : ouvre nouvelle position LONG
                extra_qty = qty - close_qty
                extra_notional = extra_qty * price
                extra_fee = self._fee(extra_notional)
                # ── Vérifier le cash AVANT d'appliquer quoi que ce soit ──
                # Le cash disponible après le rachat = cash - coût du rachat.
                # Il faut que le cash couvre TOUT (close short + extra long).
                total_cost = close_notional + close_fee + (extra_notional + extra_fee if extra_qty > 0 else 0)
                if total_cost > self.cash:
                    return {"symbol": symbol, "side": side, "qty": qty, "price": price,
                            "fee": 0.0, "pnl": 0.0, "rejected": True, "reason": "insufficient_cash"}
                # ── Appliquer le close short (état intact si rejeté ci-dessus) ──
                pnl = (pos.avg_price - price) * close_qty - close_fee
                pos.qty += close_qty
                self.realized_pnl += pnl
                self.cash -= close_notional + close_fee
                # ── Appliquer la partie excédentaire (nouveau long) ──
                if extra_qty > 0:
                    pos.avg_price = price
                    pos.qty = extra_qty
                    pos.side = "buy"
                    self.cash -= extra_notional + extra_fee
                elif pos.qty == 0:
                    pos.side = ""
                    pos.avg_price = 0.0
            else:
                # Ouvrir/augmenter LONG
                if pos.qty != 0:
                    pos.avg_price = (pos.avg_price * pos.qty + qty * price) / (pos.qty + qty)
                else:
                    pos.avg_price = price
                pos.qty += qty
                pos.side = "buy"
                self.cash -= notional + fee
        else:  # sell
            if pos.qty > 0:
                # Fermer/réduire LONG
                close_qty = min(qty, pos.qty)
                close_notional = close_qty * price
                close_fee = self._fee(close_notional)
                pnl = (price - pos.avg_price) * close_qty - close_fee
                pos.qty -= close_qty
                self.realized_pnl += pnl
                self.cash += close_notional - close_fee
                # Partie excédentaire : ouvre nouvelle position SHORT
                extra_qty = qty - close_qty
                if extra_qty > 0:
                    extra_notional = extra_qty * price
                    extra_fee = self._fee(extra_notional)
                    pos.avg_price = price
                    pos.qty = -extra_qty
                    pos.side = "sell"
                    self.cash += extra_notional - extra_fee
                elif pos.qty == 0:
                    pos.side = ""
                    pos.avg_price = 0.0
            else:
                # Ouvrir/augmenter SHORT
                if pos.qty != 0:
                    pos.avg_price = (pos.avg_price * (-pos.qty) + qty * price) / (-pos.qty + qty)
                else:
                    pos.avg_price = price
                pos.qty -= qty
                pos.side = "sell"
                self.cash += notional - fee

        trade = {"symbol": symbol, "side": side, "qty": qty, "price": price,
                 "fee": fee, "pnl": pnl}
        self.trades.append(trade)
        return trade

    def equity(self, marks: dict[str, float]) -> tuple[float, float]:
        unreal = 0.0
        market_value = 0.0
        for sym, pos in self.positions.items():
            if pos.qty == 0:
                continue
            mp = marks.get(sym, pos.avg_price)
            if pos.side == "buy":
                unreal += (mp - pos.avg_price) * pos.qty
                market_value += pos.qty * mp
            else:
                # Un short est une dette : on a reçu qty*avg_price en cash à l'ouverture,
                # et on devra racheter qty au prix de marché. La valeur de rachat (qty*mp)
                # doit être SOUSTRAITE du cash, pas ajoutée.
                unreal += (pos.avg_price - mp) * (-pos.qty)
                market_value -= (-pos.qty) * mp  # dette = abs(qty) * market price
        return self.cash + market_value, unreal
