"""Boucle de trading — orchestre data -> strat -> LLM -> broker -> metriques."""
from __future__ import annotations

import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import pandas as pd
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..config import Settings
from ..data.aggregator import DataAggregator
from ..data.reddit_client import RedditClient
from ..agent.openrouter_client import configure as or_configure
from ..data import groq_client as client_groq
from ..db import Database
from ..metrics import (
    API_LATENCY, COMPOSITE_SENT, EQUITY, ERRORS, FEAR_GREED,
    LOOP_DURATION, OPEN_POSITIONS, ORDERS, PRICE, REALIZED_PNL,
    REDDIT_SENT, SCORE, FUTURES_LS_RATIO, TRADES_TOTAL, UNREALIZED_PNL,
)
from ..strategy.momentum_sentiment import MomentumSentimentStrategy, StrategyConfig
from ..broker.paper_broker import PaperBroker
from ..broker.cryptocom_client import CryptoComClient
from ..telegram_bot import TelegramNotifier
from .openrouter_client import OpenRouterAgent

import sys
import io
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

console = Console()


class _TelegramQueue:
    def __init__(self, notifier: TelegramNotifier) -> None:
        self._notifier = notifier
        self._q: queue.Queue[str | None] = queue.Queue(maxsize=200)
        self._stop = False
        self._drop_count = 0
        self._thread = threading.Thread(target=self._worker, daemon=True, name="telegram-sender")
        self._thread.start()

    def send(self, msg: str) -> None:
        if self._stop:
            return
        try:
            self._q.put_nowait(msg)
        except queue.Full:
            self._drop_count += 1
            if self._drop_count <= 3 or self._drop_count % 50 == 0:
                print(f"[TG] Queue pleine, message perdu (cumul: {self._drop_count})")

    def _worker(self) -> None:
        while not self._stop or not self._q.empty():
            try:
                msg = self._q.get(timeout=0.5)
            except Exception:
                continue
            if msg is None:
                break
            try:
                self._notifier.send_sync(msg)
            except Exception as e:
                print(f"[TG] Erreur envoi: {e}")

    def stop(self) -> None:
        self._stop = True
        self._q.put(None)
        self._thread.join(timeout=5)


_vol_cache: dict[str, dict] = {}
_VOL_TTL = 300.0


def _calculate_volatility(ohlcv) -> float:
    if ohlcv is None or (hasattr(ohlcv, "empty") and ohlcv.empty) or (
        isinstance(ohlcv, (list, tuple)) and len(ohlcv) < 2
    ):
        return 0.05
    if isinstance(ohlcv, pd.DataFrame):
        col = "Close" if "Close" in ohlcv.columns else "close" if "close" in ohlcv.columns else None
        if col is None:
            return 0.05
        prices = ohlcv[col].dropna().tolist()
    elif isinstance(ohlcv, (list, tuple)):
        prices = []
        for c in ohlcv:
            if isinstance(c, dict):
                prices.append(c.get("close") or c.get("Close") or 0)
            elif isinstance(c, (list, tuple)) and len(c) >= 5:
                prices.append(c[4])
    else:
        return 0.05
    if len(prices) < 2:
        return 0.05
    returns = [prices[i] / prices[i - 1] - 1 for i in range(1, len(prices))]
    return (sum(r ** 2 for r in returns) / len(returns)) ** 0.5


def _vol_cached(symbol: str, ohlcv) -> float:
    entry = _vol_cache.get(symbol)
    if entry and time.time() - entry["ts"] < _VOL_TTL:
        return entry["vol"]
    vol = _calculate_volatility(ohlcv)
    _vol_cache[symbol] = {"vol": vol, "ts": time.time()}
    return vol


class TradingAgent:
    def __init__(self, settings: Settings):
        self.s = settings
        cfg = settings.raw

        strat = cfg.get("strategy", {})
        weights = strat.get("weights", {})
        mom_cfg = strat.get("momentum", {})
        thresh = strat.get("thresholds", {})
        sent_cfg = strat.get("sentiment", {})
        risk = cfg.get("risk", {})

        sc = StrategyConfig(
            w_momentum=weights.get("momentum", 0.55),
            w_sentiment=weights.get("sentiment", 0.20),
            w_fear_greed=weights.get("fear_greed", 0.25),
            lookback=mom_cfg.get("lookback_days", 14),
            ema_smooth=mom_cfg.get("ema_smooth", 7),
            threshold_long=thresh.get("long", 0.15),
            threshold_short=thresh.get("short", -0.35),
            close_threshold=thresh.get("close_threshold", 0.00),
            close_short_threshold=thresh.get("close_short_threshold", -0.10),
            allow_short=risk.get("allow_short", False),
            high_conviction=sent_cfg.get("high_conviction", False),
            min_active_sentiment_sources=sent_cfg.get("min_active_sources", 1),
            require_aligned=sent_cfg.get("require_aligned", True),
            min_momentum_abs=sent_cfg.get("min_momentum_abs", 0.05),
            enable_trend_filter=mom_cfg.get("enable_trend_filter", True),
            sma_fast=mom_cfg.get("sma_fast", 24),
            sma_slow=mom_cfg.get("sma_slow", 96),
        )
        self.strategy = MomentumSentimentStrategy(sc)

        client_groq.configure(settings.groq_api_key)
        or_configure(settings.groq_api_key, settings.openrouter_api_key)

        self.aggregator = DataAggregator(
            reddit=RedditClient(
                settings.reddit_client_id,
                settings.reddit_client_secret,
                settings.reddit_user_agent,
            ),
            reddit_subs=sent_cfg.get("reddit_subs", ["CryptoCurrency"]),
            reddit_limit=sent_cfg.get("reddit_limit", 50),
        )

        self.initial_capital = float(risk.get("initial_capital", 100.0))
        self.mode = cfg.get("mode", "paper")
        self.paper = PaperBroker(initial_cash=self.initial_capital)
        self._broker_lock = threading.RLock()

        self.exchange = CryptoComClient(
            settings.cryptocom_api_key,
            settings.cryptocom_api_secret,
            sandbox=settings.cryptocom_sandbox,
        )

        self._tg_notifier = TelegramNotifier(
            settings.telegram_token,
            settings.telegram_chat_id,
            agent=self,
        )
        self._tg = _TelegramQueue(self._tg_notifier)

        llm_cfg = cfg.get("llm", {})
        self.llm = OpenRouterAgent(
            settings.openrouter_api_key,
            llm_cfg.get("model", settings.openrouter_model),
            llm_cfg.get("temperature", 0.2),
        )
        self.validate_signals = llm_cfg.get("validate_signals", True)
        self._llm_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-val")

        self.max_position_usd = float(risk.get("max_position_usd", 30))
        self.kelly_fraction = float(risk.get("kelly_fraction", 0.25))
        self.stop_loss_pct = float(risk.get("stop_loss_pct", 0.040))
        self.take_profit_pct = float(risk.get("take_profit_pct", 0.080))
        self.short_stop_loss_pct = float(risk.get("short_stop_loss_pct", 0.035))
        self.short_take_profit_pct = float(risk.get("short_take_profit_pct", 0.070))
        self.max_concurrent_positions = int(risk.get("max_concurrent_positions", 3))
        self.equity_stop_loss_pct = float(risk.get("equity_stop_loss_pct", 0.10))
        # Cooldown anti-revenge-trade après un stop-loss (en minutes)
        self.cooldown_after_sl_min = float(risk.get("cooldown_after_sl_min", 0))
        self.max_hold_hours = float(risk.get("max_hold_hours", 48))
        self._sl_cooldown: dict[str, float] = {}
        self._entry_time: dict[str, float] = {}
        self._trailing_high: dict[str, float] = {}  # plus haut prix atteint par position LONG
        self._trailing_low: dict[str, float] = {}   # plus bas prix atteint par position SHORT
        self._stop_triggered = False
        self._paused = False

        self.db = Database(settings.sqlite_path)
        self._restore_positions()

        self._snapshots_lock = threading.Lock()
        self._last_snapshots: dict[str, Any] = {}
        self._marks_lock = threading.Lock()
        self._last_prices: dict[str, float] = {}
        self._max_price_drop_pct = 0.30  # ignorer les ticks qui chutent de >30% d'un coup

        self._step_count = 0
        self._summary_interval = max(1, int(cfg.get("telegram", {}).get("summary_interval_min", 10)))
        self._summary_steps = self._summary_interval * 60 // max(1, self.s.loop_interval)
        self._last_summary_hash: str | None = None

        self._equity_history: list[float] = [self.initial_capital]
        self._snap_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="snap")

    def _restore_positions(self) -> None:
        rows = self.db.load_positions()
        if not rows:
            return
        from ..broker.paper_broker import Position
        from datetime import datetime, timezone
        for row in rows:
            # Nouveau format: (symbol, side, qty, avg_price, entry_ts)
            if len(row) == 5:
                symbol, side, qty, avg_price, entry_ts = row
            else:  # ancien format: (symbol, side, qty, avg_price)
                symbol, side, qty, avg_price = row
                entry_ts = None
            pos = Position(symbol=symbol, side=side, qty=qty, avg_price=avg_price)
            self.paper.positions[symbol] = pos
            # Restaurer le timestamp d'entrée pour le time-based exit
            if entry_ts:
                try:
                    ts_dt = datetime.fromisoformat(entry_ts)
                    if ts_dt.tzinfo is None:
                        ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                    self._entry_time[symbol] = ts_dt.timestamp()
                except Exception:
                    self._entry_time[symbol] = time.time()
            else:
                # Position sans timestamp connu → on utilise maintenant
                self._entry_time[symbol] = time.time()
        # Recalculer le cash : les longs ont coûté, les shorts ont rapporté
        cash_adjust = 0.0
        for p in self.paper.positions.values():
            if p.qty > 0:  # long → on a payé
                cash_adjust -= p.qty * p.avg_price
            elif p.qty < 0:  # short → on a reçu
                cash_adjust += abs(p.qty) * p.avg_price
        self.paper.cash += cash_adjust
        self._log(f"[dim]{len(rows)} position(s) restaurée(s) (cash ajusté de {cash_adjust:+.2f}$) depuis la BDD[/]")
        # ── Sécurité anti-crash : vérifier les prix de marché des positions restaurées ──
        # Si le bot a été arrêté longtemps, une position peut avoir dévié dangereusement.
        from ..data.ohlcv_client import fetch_ohlcv
        self._log("[yellow]Vérification des positions restaurées (SL de sécurité)...[/]")
        with self._broker_lock:
            for symbol in list(self.paper.positions.keys()):
                pos = self.paper.positions.get(symbol)
                if not pos or abs(pos.qty) == 0:
                    continue
                try:
                    df = fetch_ohlcv(symbol, period="1d", interval="1h")
                    if df.empty:
                        continue
                    current_price = float(df["Close"].iloc[-1])
                    if current_price <= 0:
                        continue
                    if pos.side == "buy":
                        pnl_pct = current_price / pos.avg_price - 1
                    else:
                        pnl_pct = pos.avg_price / current_price - 1
                    # Si la perte dépasse 2× le stop_loss_pct → fermeture d'urgence
                    sl_emergency = self.stop_loss_pct * 2.0 if hasattr(self, 'stop_loss_pct') else 0.08
                    if pnl_pct < -sl_emergency:
                        self._log(f"[bold red]SL DE SÉCURITÉ {symbol}: perte de {pnl_pct*100:.1f}% pendant l'arrêt → fermeture[/]")
                        close_side = "sell" if pos.side == "buy" else "buy"
                        tr = self.paper.market(symbol, close_side, abs(pos.qty), current_price)
                        self.db.insert_trade(symbol, close_side, abs(pos.qty), current_price, "paper", fee=tr["fee"], pnl=tr["pnl"])
                        del self.paper.positions[symbol]
                        self._tg.send(f"🛑 SL sécurité {symbol}\n| Perte {pnl_pct*100:.1f}% pendant arrêt\n| Fermeture @ ${current_price:.2f}")
                except Exception as e:
                    self._log(f"[red]Erreur vérification sécurité {symbol}: {e}[/]")

    def _log(self, msg: str) -> None:
        console.log(msg)

    def _fmt(self, x: float | None) -> str:
        return f"{x:+.3f}" if x is not None else "  . "

    def _build_display(self) -> Panel:
        table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 1))
        for col, kw in [("Asset", {}), ("Price", {"justify": "right"}), ("Score", {"justify": "right"}),
                        ("Decision", {"justify": "center"}), ("Sentiment", {"justify": "right"}),
                        ("LS Ratio", {"justify": "right"}), ("F&G", {"justify": "right"}), ("Pos.", {"justify": "right"})]:
            table.add_column(col, **kw)
        with self._snapshots_lock:
            snap_copy = dict(self._last_snapshots)
        for symbol in self.s.universe:
            d = snap_copy.get(symbol, {})
            price = d.get("price", 0)
            score = d.get("score")
            decision = d.get("decision", "-")
            pos_qty = d.get("pos_qty", 0)
            pos_pnl = d.get("pos_pnl", 0)
            sc = "green" if score and score > 0 else "red" if score and score < 0 else "white"
            dc = {"LONG": "bold green", "SHORT": "bold red", "FLAT": "dim white"}.get(decision, "white")
            ps = f"[{'green' if pos_pnl > 0 else 'red'}]{pos_qty:.4f}[/]" if pos_qty > 0 else f"{pos_qty:.4f}"
            table.add_row(
                f"[bold]{symbol.split('-')[0]}[/]",
                f"${price:,.2f}" if price > 0 else "-",
                f"[{sc}]{score:+.3f}[/]" if score is not None else "-",
                f"[{dc}]{decision}[/]",
                self._fmt(d.get("sentiment")),
                self._fmt(d.get("futures_ls")),
                self._fmt(d.get("fear_greed")),
                ps,
            )
        with self._broker_lock:
            equity, unreal = self.paper.equity({s: d.get("price", 0) for s, d in snap_copy.items()})
            cash = self.paper.cash
            rpnl = self.paper.realized_pnl
            ntrades = len(self.paper.trades)
        summary = (
            f"[bold]Mode:[/] {self.mode}  [bold]Cash:[/] ${cash:.2f}  "
            f"[bold]Equity:[/] ${equity:.2f}  [bold]P&L Réalisé:[/] ${rpnl:.2f}  "
            f"[bold]P&L Non-réalisé:[/] ${unreal:.2f}  [bold]Trades:[/] {ntrades}"
        )
        return Panel(table, title="[bold yellow]TradingBot CDC[/]", subtitle=summary, border_style="blue")

    def _send_telegram_summary(self) -> None:
        with self._snapshots_lock:
            snap_copy = dict(self._last_snapshots)
        with self._broker_lock:
            equity, unreal = self.paper.equity({s: d.get("price", 0) for s, d in snap_copy.items()})
            cash = self.paper.cash
            pos_count = sum(1 for p in self.paper.positions.values() if abs(p.qty) > 0)
            rpnl = self.paper.realized_pnl
            ntrades = len(self.paper.trades)
        perf = (equity / self.initial_capital - 1) * 100
        signals = [
            f"  {s.split('-')[0]} -> {d['decision']} (score={d['score']:+.3f})"
            for s in self.s.universe
            if (d := snap_copy.get(s, {})) and d.get("decision") in ("LONG", "SHORT")
        ]
        signals_str = "\n".join(signals[:6]) or "  Aucun signal actif"
        issues = [
            f"  (i) {s.split('-')[0]} : données manquantes"
            for s in self.s.universe
            if snap_copy.get(s, {}).get("decision") == "-"
        ]
        msg = (
            f"Résumé périodique\n"
            f"| Cash : ${cash:.2f}\n"
            f"| Equity : ${equity:.2f} ({perf:+.3f}%)\n"
            f"| Positions : {pos_count}\n"
            f"| P&L Réalisé : ${rpnl:+.2f}\n"
            f"| P&L Non-réalisé : ${unreal:+.2f}\n"
            f"| Trades : {ntrades}\n"
            f"| Signaux :\n{signals_str}"
            + ("\n" + "\n".join(issues) if issues else "")
        )
        h = f"{cash:.2f}|{pos_count}|{ntrades}|{signals_str}"
        if h == self._last_summary_hash:
            return
        self._last_summary_hash = h
        self._tg.send(msg)

    def _execute(self, symbol: str, side: str, qty: float, price: float) -> dict[str, Any]:
        if price <= 0 or qty <= 0:
            self._log(f"[yellow](i) Ordre ignoré {symbol} {side} qty={qty} price={price} (valeurs invalides)[/]")
            return {}
        ORDERS.labels(side=side, symbol=symbol).inc()
        if self.mode == "paper":
            with self._broker_lock:
                tr = self.paper.market(symbol, side, qty, price)
            if tr.get("rejected"):
                self._log(f"[yellow](i) Ordre rejeté {symbol} {side}: {tr.get('reason', 'unknown')}[/]")
                return tr
            self.db.insert_trade(symbol, side, qty, price, "paper", fee=tr["fee"], pnl=tr["pnl"])
            pnl_str = f" P&L={tr['pnl']:+.2f}" if tr["pnl"] != 0 else ""
            self._log(f"[cyan]{symbol} {side.upper()} {qty} @ ${price:.2f}{pnl_str}[/]")
            if tr.get("pnl", 0) != 0:
                emoji = "(g)" if tr["pnl"] > 0 else "(r)"
                self._tg.send(f"{emoji} Trade fermé : {symbol}\n| {side.upper()} {qty:.4f} @ ${price:.2f}\n| P&L : ${tr['pnl']:+.2f}")
            else:
                self._tg.send(f"Nouveau trade : {symbol}\n| {side.upper()} {qty:.4f} @ ${price:.2f}")
            return tr
        try:
            t0 = time.time()
            res = self.exchange.place_order(symbol, side, qty, price=price, order_type="LIMIT", client_order_id=str(uuid.uuid4()))
            API_LATENCY.labels(endpoint="place_order").observe(time.time() - t0)
            self.db.insert_trade(symbol, side, qty, price, "live", order_id=str(res.get("order_id", "")))
            with self._broker_lock:
                tr = self.paper.market(symbol, side, qty, price)
            pnl_str = f" P&L={tr['pnl']:+.2f}" if tr["pnl"] != 0 else ""
            self._log(f"[cyan]{symbol} {side.upper()} {qty} @ ${price:.2f} (ordre={res.get('order_id','?')}){pnl_str}[/]")
            if tr.get("pnl", 0) != 0:
                emoji = "(g)" if tr["pnl"] > 0 else "(r)"
                self._tg.send(f"{emoji} Trade fermé (live) : {symbol}\n| {side.upper()} {qty:.4f} @ ${price:.2f}\n| P&L : ${tr['pnl']:+.2f}")
            else:
                self._tg.send(f"Nouveau trade (live) : {symbol}\n| {side.upper()} {qty:.4f} @ ${price:.2f}\n| Ordre : {res.get('order_id','?')}")
            return {**res, **tr}
        except Exception as e:
            ERRORS.labels(component="cryptocom").inc()
            self._log(f"[red]Crypto.com : échec de l'ordre : {e}[/]")
            return {}

    def _is_night_trade(self) -> bool:
        """Vérifie si on est en période night trade (20h-4h UTC)."""
        hour = time.gmtime().tm_hour
        return hour >= 20 or hour < 4

    def _position_size(self, symbol: str, price: float, volatility: float, score: float = 0.0) -> float:
        with self._broker_lock:
            equity, _ = self.paper.equity({})
            cash = self.paper.cash

        # Sizing : risque de portefeuille cible = 1% de l'equity
        # On convertit en notionnel via le stop-loss, borné par max_position_usd et levier implicite.
        risk_target = max(equity * 0.01, 0.0)
        stop_dist = price * max(self.stop_loss_pct, 1e-9)
        if volatility > 0:
            # Si la volatilité est élevée, on réduit la taille (risque proportionnel à vol)
            vol_adj = min(1.0, 0.05 / max(volatility, 1e-9))
            stop_dist = max(stop_dist, price * vol_adj)
        qty = risk_target / max(stop_dist, 1e-12)
        max_notional = min(equity * 0.25, self.max_position_usd)
        notional = min(qty * price, max_notional)
        # ── Bug 8 corrigé : vérifier que le cash disponible suffit ──
        # Le notionnel (hors frais) ne doit pas dépasser le cash disponible.
        # On borne le notionnel au cash pour éviter de dépendre du rejet du PaperBroker.
        notional = min(notional, max(cash, 0.0))
        return max(notional, 0.0)

    def _get_spot_price(self, symbol: str) -> float | None:
        """Tente un prix spot direct depuis Binance, indépendant de l'OHLCV."""
        try:
            from ..data import binance_client
            price = binance_client.price(symbol)
            if price and float(price) > 0:
                return float(price)
        except Exception:
            pass
        return None

    def _process_symbol(self, symbol: str, marks: dict[str, float], marks_lock: threading.Lock, readonly: bool = False) -> None:
        try:
            snap = self.aggregator.snapshot(symbol)
        except Exception as e:
            ERRORS.labels(component="data").inc()
            self._log(f"[red]Erreur de données {symbol}: {e}[/]")
            self._tg.send(f"Erreur de données {symbol} : {str(e)[:100]}")
            return
        if snap.price <= 0.01:
            return
        # Utiliser le prix spot Binance si disponible (plus réactif que l'OHLCV)
        spot = self._get_spot_price(symbol)
        if spot is not None:
            price = spot
        else:
            price = snap.price
        # ── Validation anti-prix-aberrant ──
        # Si le prix chute OU monte de plus de _max_price_drop_pct par rapport au
        # dernier prix connu, on ignore ce tick (probable donnée corrompue) et on
        # garde l'ancien prix. Protège contre les faux SL/TP/trailing/buy.
        prev = self._last_prices.get(symbol)
        if prev and prev > 0:
            change = (price - prev) / prev
            if abs(change) > self._max_price_drop_pct:
                direction = "hausse" if change > 0 else "chute"
                self._log(f"[yellow](i) Prix aberrant ignoré {symbol}: {price:.4f} ({direction} de {abs(change)*100:.1f}% vs {prev:.4f})[/]")
                price = prev
        self._last_prices[symbol] = price
        with marks_lock:
            marks[symbol] = price
        PRICE.labels(symbol=symbol).set(price)
        snap.price = price
        if snap.reddit is not None:
            REDDIT_SENT.labels(symbol=symbol).set(snap.reddit)
        if snap.futures_ls is not None:
            FUTURES_LS_RATIO.labels(symbol=symbol).set(snap.futures_ls)
        if snap.fear_greed is not None:
            FEAR_GREED.set((snap.fear_greed + 1) * 50)
        with self._broker_lock:
            current_pos = self.paper.positions.get(symbol)
            pos_side = current_pos.side if current_pos and abs(current_pos.qty) > 0 else ""
        sig = self.strategy.evaluate(snap.ohlcv, reddit=snap.reddit, futures_ls=snap.futures_ls, coingecko=snap.coingecko_social, fear_greed=snap.fear_greed, binance_change=snap.binance_change, binance_taker=snap.binance_taker, symbol=symbol, position_side=pos_side)
        SCORE.labels(symbol=symbol).set(sig.score)
        COMPOSITE_SENT.labels(symbol=symbol).set(sig.sentiment)
        self.db.record_signal(symbol, sig.score, sig.momentum, sig.sentiment, sig.fear_greed, sig.decision)
        with self._broker_lock:
            pos = self.paper.positions.get(symbol)
            pos_qty = pos.qty if pos else 0
            pos_pnl = 0.0
            if pos and abs(pos.qty) > 0:
                mp = marks.get(symbol, pos.avg_price)
                pos_pnl = (mp - pos.avg_price) * abs(pos.qty) if pos.side == "buy" else (pos.avg_price - mp) * abs(pos.qty)
        with self._snapshots_lock:
            self._last_snapshots[symbol] = {"price": snap.price, "score": sig.score, "decision": sig.decision, "sentiment": sig.sentiment, "futures_ls": snap.futures_ls, "fear_greed": snap.fear_greed, "pos_qty": pos_qty, "pos_pnl": pos_pnl}
        if readonly:
            return  # juste collecter les scores, pas de trade
        if pos and abs(pos.qty) > 0:
            entry = pos.avg_price
            abs_qty = abs(pos.qty)
            # ── Time-based exit : fermer si la position dure trop longtemps ──
            if symbol in self._entry_time:
                hold_hours = (time.time() - self._entry_time[symbol]) / 3600
                if hold_hours >= self.max_hold_hours:
                    self._log(f"[yellow]Time-based exit {symbol} (hold={hold_hours:.1f}h > {self.max_hold_hours}h)[/]")
                    close_side = "sell" if pos.side == "buy" else "buy"
                    self._execute(symbol, close_side, abs_qty, snap.price)
                    # Nettoyer tous les états associés à la position
                    self._entry_time.pop(symbol, None)
                    self._trailing_high.pop(symbol, None)
                    self._trailing_low.pop(symbol, None)
                    return
            if pos.side == "buy":
                # ── Mettre à jour le high-water mark (plus haut prix atteint) ──
                prev_high = self._trailing_high.get(symbol, entry)
                self._trailing_high[symbol] = max(prev_high, snap.price)
                high = self._trailing_high[symbol]
                # ── Trailing stop : verrouille 50% des gains si le prix a dépassé 66% du TP ──
                pct_to_tp = (high - entry) / (entry * self.take_profit_pct) if entry > 0 else 0
                if pct_to_tp >= 0.66:
                    trailing_sl = entry + (high - entry) * 0.5  # lock 50% du gain max
                    if snap.price <= trailing_sl:
                        self._log(f"[cyan]Trailing-lock50 {symbol} @ ${snap.price:.2f} (haut={high:.2f})[/]")
                        self._tg.send(f"🔒 Trailing-lock {symbol}\n| Entrée : ${entry:.2f}\n| Sortie : ${snap.price:.2f}")
                        self._execute(symbol, "sell", abs_qty, snap.price)
                        self._trailing_high.pop(symbol, None)
                        return
                # ── Breakeven : protège le capital si le prix a dépassé 33% du TP ──
                elif pct_to_tp >= 0.33:
                    breakeven = entry * 1.002  # couvre frais
                    if snap.price <= breakeven:
                        self._log(f"[cyan]Breakeven {symbol} @ ${snap.price:.2f}[/]")
                        self._execute(symbol, "sell", abs_qty, snap.price)
                        self._trailing_high.pop(symbol, None)
                        return
                # ── Stop-loss fixe (protection de base) ──
                if snap.price <= entry * (1 - self.stop_loss_pct):
                    self._log(f"[yellow]Stop-loss {symbol} @ ${snap.price:.2f}[/]")
                    self._tg.send(f"Stop-loss {symbol}\n| Entrée : ${entry:.2f}\n| Sortie : ${snap.price:.2f} ({((snap.price/entry-1)*100):+.2f}%)")
                    self._execute(symbol, "sell", abs_qty, snap.price)
                    self._trailing_high.pop(symbol, None)
                    if self.cooldown_after_sl_min > 0:
                        self._sl_cooldown[symbol] = time.time() + self.cooldown_after_sl_min * 60
                    return
                # ── Take-profit fixe ──
                elif snap.price >= entry * (1 + self.take_profit_pct):
                    self._log(f"[green]Take-profit {symbol} @ ${snap.price:.2f}[/]")
                    self._tg.send(f"Take-profit {symbol}\n| Entrée : ${entry:.2f}\n| Sortie : ${snap.price:.2f} ({((snap.price/entry-1)*100):+.2f}%)")
                    self._execute(symbol, "sell", abs_qty, snap.price)
                    self._trailing_high.pop(symbol, None)
                    return
            elif pos.side == "sell":
                sl_pct = getattr(self, 'short_stop_loss_pct', self.stop_loss_pct)
                tp_pct = getattr(self, 'short_take_profit_pct', self.take_profit_pct)
                # ── Mettre à jour le low-water mark (plus bas prix atteint) ──
                prev_low = self._trailing_low.get(symbol, entry)
                self._trailing_low[symbol] = min(prev_low, snap.price)
                low = self._trailing_low[symbol]
                # ── Trailing stop SHORT : verrouille 50% des gains si le prix a dépassé 66% du TP ──
                pct_to_tp = (entry - low) / (entry * tp_pct) if entry > 0 else 0
                if pct_to_tp >= 0.66:
                    trailing_sl = entry - (entry - low) * 0.5  # lock 50% du gain max
                    if snap.price >= trailing_sl:
                        self._log(f"[cyan]Trailing-lock50 short {symbol} @ ${snap.price:.2f} (bas={low:.2f})[/]")
                        self._tg.send(f"🔒 Trailing-lock short {symbol}")
                        self._execute(symbol, "buy", abs_qty, snap.price)
                        self._trailing_low.pop(symbol, None)
                        return
                # ── Breakeven SHORT : protège le capital ──
                elif pct_to_tp >= 0.33:
                    breakeven = entry * 0.998
                    if snap.price >= breakeven:
                        self._log(f"[cyan]Breakeven short {symbol} @ ${snap.price:.2f}[/]")
                        self._execute(symbol, "buy", abs_qty, snap.price)
                        self._trailing_low.pop(symbol, None)
                        return
                # ── Stop-loss fixe SHORT ──
                if snap.price >= entry * (1 + sl_pct):
                    self._log(f"[yellow]Stop-loss short {symbol} @ ${snap.price:.2f}[/]")
                    self._tg.send(f"Stop-loss short {symbol}")
                    self._execute(symbol, "buy", abs_qty, snap.price)
                    self._trailing_low.pop(symbol, None)
                    return
                # ── Take-profit fixe SHORT ──
                elif snap.price <= entry * (1 - tp_pct):
                    self._log(f"[green]Take-profit short {symbol} @ ${snap.price:.2f}[/]")
                    self._tg.send(f"Take-profit short {symbol}")
                    self._execute(symbol, "buy", abs_qty, snap.price)
                    self._trailing_low.pop(symbol, None)
                    return
        with self._broker_lock:
            eq_now, _ = self.paper.equity(marks)
        eq_change = (eq_now / self.initial_capital) - 1.0
        if eq_change < -self.equity_stop_loss_pct:
            if not self._stop_triggered:
                self._stop_triggered = True
                self._log(f"[bold red]STOP-LOSS GLOBAL DECLENCHÉ ({eq_change*100:.2f}%) - Fermeture de toutes les positions[/]")
                self._tg.send(f"🔴 STOP-LOSS GLOBAL ({eq_change*100:.2f}%) - Fermeture de toutes les positions")
                with self._broker_lock:
                    for sym, p in list(self.paper.positions.items()):
                        if p.qty != 0:
                            close_side = "sell" if p.side == "buy" else "buy"
                            self._execute(sym, close_side, abs(p.qty), marks.get(sym, p.avg_price))
                            # Nettoyer l'état associé à la position fermée
                            self._entry_time.pop(sym, None)
                            self._trailing_high.pop(sym, None)
                            self._trailing_low.pop(sym, None)
            return
        self._stop_triggered = False

        threshold_long = self.strategy.cfg.threshold_long

        if sig.decision == "LONG":
            if sig.score < threshold_long:
                return
            cd = self._sl_cooldown.get(symbol)
            if cd and time.time() < cd:
                return
            # Vérification atomique sous RLock (réentrant, compatible avec _execute)
            with self._broker_lock:
                current_open = sum(1 for p in self.paper.positions.values() if p.qty != 0)
                if current_open >= self.max_concurrent_positions:
                    self._log(f"[dim]LONG {symbol} bloqué: max positions ({self.max_concurrent_positions}) atteint[/]")
                    return
                pos = self.paper.positions.get(symbol)
                if pos and pos.qty > 0 and pos.side == "buy":
                    return
            if self.validate_signals and abs(sig.score) < 0.30:
                fut = self._llm_executor.submit(self.llm.validate, {"score": sig.score, "momentum": sig.momentum, "sentiment": sig.sentiment, "fear_greed": sig.fear_greed}, "buy")
                try:
                    if not fut.result(timeout=8.0)["approve"]:
                        return
                except Exception:
                    pass
            vol = _vol_cached(symbol, snap.ohlcv)
            notional = self._position_size(symbol, snap.price, vol, sig.score)
            qty = round(notional / snap.price, 6)
            if qty > 0:
                self._log(f"[bold green]LONG {symbol} @ ${snap.price:.2f} (qty={qty})[/] mom={sig.momentum:+.3f} score={sig.score:+.3f}")
                self._execute(symbol, "buy", qty, snap.price)
                self._entry_time[symbol] = time.time()
                self._trailing_high[symbol] = snap.price
                self._trailing_low.pop(symbol, None)
        elif sig.decision == "SHORT":
            if sig.score > self.strategy.cfg.threshold_short:
                return
            with self._broker_lock:
                current_open = sum(1 for p in self.paper.positions.values() if p.qty != 0)
                if current_open >= self.max_concurrent_positions:
                    self._log(f"[dim]SHORT {symbol} bloqué: max positions ({self.max_concurrent_positions}) atteint[/]")
                    return
                pos = self.paper.positions.get(symbol)
                if pos and pos.qty < 0 and pos.side == "sell":
                    return
            if self.validate_signals and abs(sig.score) < 0.30:
                fut = self._llm_executor.submit(self.llm.validate, {"score": sig.score, "momentum": sig.momentum, "sentiment": sig.sentiment, "fear_greed": sig.fear_greed}, "sell")
                try:
                    if not fut.result(timeout=8.0)["approve"]:
                        return
                except Exception:
                    pass
            vol = _vol_cached(symbol, snap.ohlcv)
            notional = self._position_size(symbol, snap.price, vol, sig.score)
            qty = round(notional / snap.price, 6)
            if qty > 0:
                self._log(f"[bold red]SHORT {symbol} @ ${snap.price:.2f} (qty={qty})[/]")
                self._execute(symbol, "sell", qty, snap.price)
                self._entry_time[symbol] = time.time()
                self._trailing_low[symbol] = snap.price
                self._trailing_high.pop(symbol, None)
        elif sig.decision == "FLAT":
            with self._broker_lock:
                pos = self.paper.positions.get(symbol)
                if pos and abs(pos.qty) > 0:
                    close_side = "sell" if pos.side == "buy" else "buy"
                    qty = abs(pos.qty)
                    avg_price = pos.avg_price
                else:
                    qty = 0
                    close_side = "sell"
                    avg_price = 0.0
            if qty > 0:
                self._log(f"[dim]Fermeture {symbol} ({qty} @ ${avg_price:.2f}) - FLAT[/]")
                self._execute(symbol, close_side, qty, snap.price)
                self._entry_time.pop(symbol, None)
                self._trailing_high.pop(symbol, None)
                self._trailing_low.pop(symbol, None)

    @LOOP_DURATION.time()
    def step(self) -> None:
        # Étape 1 : scanner TOUS les symboles en readonly pour collecter les scores
        all_marks: dict[str, float] = {}
        marks_lock = threading.Lock()
        all_futures = {self._snap_executor.submit(self._process_symbol, sym, all_marks, marks_lock, readonly=True): sym for sym in self.s.universe}
        from concurrent.futures import wait
        done, _ = wait(list(all_futures.keys()), timeout=300)
        for fut in done:
            try:
                fut.result()
            except Exception:
                ERRORS.labels(component="step").inc()

        with self._broker_lock:
            open_symbols = {s for s, p in self.paper.positions.items() if p.qty != 0 and s in self.s.universe}

        # Si le bot est en pause : ne gérer QUE les positions existantes (SL/TP)
        if self._paused:
            trade_set = open_symbols
            if not trade_set:
                console.log("[dim]⏸️ Bot en pause — aucun trade[/]")
                return
        else:
            # Étape 2 : trier les symboles par score
            scored = [(self._last_snapshots.get(sym, {}).get("score") or 0, sym) for sym in self.s.universe]
            scored.sort(key=lambda x: x[0])  # croissant: les + baissiers en premier

            # Étape 3 : sélectionner les max_concurrent_positions opportunités
            sel_short = self.strategy.cfg.threshold_short
            sel_long = self.strategy.cfg.threshold_long
            max_active = self.max_concurrent_positions
            half = max(1, max_active // 2)
            shorts = [s for s in scored if s[0] < sel_short][:half]
            longs = [s for s in reversed(scored) if s[0] > sel_long][:half]
            active = list(open_symbols)
            interleaved = []
            for i in range(max(len(shorts), len(longs))):
                if i < len(shorts):
                    interleaved.append(shorts[i])
                if i < len(longs):
                    interleaved.append(longs[i])
            for score, sym in interleaved:
                if sym not in active:
                    active.append(sym)
                if len(active) >= max_active:
                    break
            active = active[:max_active]

            # Afficher les scores TOP/BOTTOM dans la console
            top5_bear = scored[:3]
            top5_bull = list(reversed(scored))[:3]
            log_bear = " | ".join(f"{s.split('-')[0]}({score:+.3f})" for score, s in top5_bear)
            log_bull = " | ".join(f"{s.split('-')[0]}({score:+.3f})" for score, s in top5_bull)
            log_sel = " | ".join(s.split('-')[0] for s in active)
            console.log(f"[dim]Scores: bear=[/][red]{log_bear}[/][dim] bull=[/][green]{log_bull}[/]")
            console.log(f"[cyan]Selected: {log_sel}[/]")

            trade_set = set(active) | open_symbols

        # Étape 4 : rescanner les symboles à trader + positions ouvertes (pour SL/TP)
        trade_marks: dict[str, float] = dict(all_marks)
        trade_futures = {self._snap_executor.submit(self._process_symbol, sym, trade_marks, marks_lock, readonly=False): sym for sym in trade_set}
        if trade_futures:
            done2, _ = wait(list(trade_futures.keys()), timeout=120)
            for fut in done2:
                try:
                    fut.result()
                except Exception:
                    ERRORS.labels(component="step").inc()
        marks = trade_marks

        with self._broker_lock:
            equity, unreal = self.paper.equity(marks)
            rpnl = self.paper.realized_pnl
            npos = sum(1 for p in self.paper.positions.values() if p.qty != 0)
            ntrades = len(self.paper.trades)
        self._equity_history.append(equity)
        EQUITY.set(equity)
        REALIZED_PNL.set(rpnl)
        UNREALIZED_PNL.set(unreal)
        OPEN_POSITIONS.set(npos)
        TRADES_TOTAL.set(ntrades)
        self.db.record_equity(equity, rpnl, unreal)
        with self._broker_lock:
            self.db.save_positions(self.paper.positions, self._entry_time)
        console.clear()
        console.print(self._build_display())

    def run_forever(self) -> None:
        # Démarrer Telegram si configuré
        if self._tg_notifier.token and self._tg_notifier.chat_id:
            self._tg_notifier.start()
        self._log(f"[green]Agent démarré - mode={self.mode} exchange={self.s.exchange}[/]")
        try:
            if self._tg_notifier.token and self._tg_notifier.chat_id:
                self._tg.send(f"Agent démarré\n| Mode : {self.mode}\n| Universe : {len(self.s.universe)} actifs\n| Capital initial : ${self.initial_capital:,.0f}\n| Seuil LONG : {self.strategy.cfg.threshold_long:+.3f}\n| Seuil SHORT : {self.strategy.cfg.threshold_short:+.3f}\n| TP : {self.take_profit_pct*100:.0f}% / SL : {self.stop_loss_pct*100:.0f}%\n| Résumé toutes les {self._summary_interval} min")
            while True:
                try:
                    self.step()
                    self._step_count += 1
                    if self._step_count % self._summary_steps == 0:
                        self._send_telegram_summary()
                except Exception as e:
                    ERRORS.labels(component="step").inc()
                    self._log(f"[red]Erreur dans step(): {e}[/]")
                time.sleep(self.s.loop_interval)
        except KeyboardInterrupt:
            try:
                self._tg.send("Agent arrêté (Ctrl+C)")
            except Exception:
                pass
            self._tg.stop()
            self._tg_notifier.stop()
            self._log("[yellow]Arrêt demandé par l'utilisateur[/]")
            console.print("\n[bold yellow]=== Résumé final ===[/]")
            with self._broker_lock:
                eq, _ = self.paper.equity({s: d.get("price", 0) for s, d in self._last_snapshots.items()})
                rpnl = self.paper.realized_pnl
                ntrades = len(self.paper.trades)
            console.print(f"Capital final : ${eq:.2f}")
            console.print(f"P&L réalisé : ${rpnl:.2f}")
            console.print(f"Trades : {ntrades}")
