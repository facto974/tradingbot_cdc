"""Paper broker — simule l'exécution en local pour tester sans clé Gemini."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Position:
    symbol: str = ""
    qty: float = 0.0
    avg_price: float = 0.0
    side: str = ""   # "buy" = long, "sell" = short, "" = pas de position


class PaperBroker:
    def __init__(self, initial_cash: float = 10_000.0, fee_bps: float = 10):
        self.cash = initial_cash
        self.fee_bps = fee_bps
        self.positions: dict[str, Position] = {}
        self.realized_pnl = 0.0
        self.trades: list[dict] = []

    def _fee(self, notional: float) -> float:
        return notional * self.fee_bps / 1e4

    def market(self, symbol: str, side: str, qty: float, price: float) -> dict:
        notional = qty * price
        fee = self._fee(notional)
        pos = self.positions.setdefault(symbol, Position(symbol=symbol))
        pnl = 0.0

        if side.lower() == "buy":
            new_qty = pos.qty + qty
            if new_qty != 0:
                pos.avg_price = (pos.avg_price * pos.qty + qty * price) / new_qty
            pos.qty = new_qty
            pos.side = "buy" if pos.qty > 0 else ""
            self.cash -= notional + fee
        else:  # sell
            if pos.qty > 0:
                close_qty = min(qty, pos.qty)
                pnl = (price - pos.avg_price) * close_qty - fee
                pos.qty -= close_qty
                self.realized_pnl += pnl
                if pos.qty == 0:
                    pos.side = ""
            else:
                # Short position
                pos.qty -= qty
                pos.avg_price = price
                pos.side = "sell"
            self.cash += notional - fee

        trade = {"symbol": symbol, "side": side, "qty": qty, "price": price,
                 "fee": fee, "pnl": pnl}
        self.trades.append(trade)
        return trade

    def equity(self, marks: dict[str, float]) -> tuple[float, float]:
        unreal = 0.0
        for sym, pos in self.positions.items():
            if pos.qty == 0:
                continue
            mp = marks.get(sym, pos.avg_price)
            if pos.side == "buy":
                unreal += (mp - pos.avg_price) * pos.qty
            else:
                unreal += (pos.avg_price - mp) * pos.qty  # short : profit si prix baisse
        return self.cash + sum(p.qty * marks.get(s, p.avg_price)
                               for s, p in self.positions.items()), unreal