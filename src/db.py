"""Persistance SQLite — trades, positions, equity points, sentiment cache."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL NOT NULL DEFAULT 0,
    pnl REAL,
    mode TEXT NOT NULL,
    order_id TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT NOT NULL,
    equity REAL NOT NULL,
    realized REAL NOT NULL,
    unrealized REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS signals (
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    score REAL NOT NULL,
    momentum REAL,
    sentiment REAL,
    fear_greed REAL,
    decision TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS positions (
    symbol TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sentiment_cache (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    ts TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str = "./data/trader.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def insert_trade(self, symbol: str, side: str, qty: float, price: float,
                     mode: str, fee: float = 0.0, pnl: float | None = None,
                     order_id: str | None = None) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO trades (ts, symbol, side, qty, price, fee, pnl, mode, order_id)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (self._now(), symbol, side, qty, price, fee, pnl, mode, order_id),
            )

    def record_equity(self, equity: float, realized: float, unrealized: float) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO equity (ts, equity, realized, unrealized) VALUES (?,?,?,?)",
                (self._now(), equity, realized, unrealized),
            )

    def record_signal(self, symbol: str, score: float, momentum: float,
                      sentiment: float, fear_greed: float, decision: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO signals (ts, symbol, score, momentum, sentiment, fear_greed, decision)"
                " VALUES (?,?,?,?,?,?,?)",
                (self._now(), symbol, score, momentum, sentiment, fear_greed, decision),
            )

    def save_positions(self, positions: dict) -> None:
        """Persiste toutes les positions ouvertes (écrase les anciennes)."""
        with self._conn() as c:
            c.execute("DELETE FROM positions")
            for symbol, pos in positions.items():
                if pos.qty > 0:
                    c.execute(
                        "INSERT OR REPLACE INTO positions (symbol, side, qty, avg_price, ts)"
                        " VALUES (?,?,?,?,?)",
                        (symbol, pos.side, pos.qty, pos.avg_price, self._now()),
                    )

    def load_positions(self) -> list[tuple]:
        """Recharge les positions persistées. Retourne [(symbol, side, qty, avg_price)]. """
        with self._conn() as c:
            cur = c.execute(
                "SELECT symbol, side, qty, avg_price FROM positions WHERE qty > 0"
            )
            return cur.fetchall()

    def fetch_trades(self, limit: int = 100) -> list[tuple]:
        with self._conn() as c:
            cur = c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,))
            return cur.fetchall()
