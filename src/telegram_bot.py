"""Bot Telegram — notifications trades + commandes en direct + graphiques colorés."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
import threading

import pandas as pd
import telegram
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .telegram_chart import equity_chart, signals_chart

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Envoie des notifications et écoute les commandes via Telegram.

    Usage ::
        notifier = TelegramNotifier(token, chat_id)
        notifier.start()            # démarre le polling en arrière-plan
        await notifier.send("📈 Trade exécuté")   # notification
        notifier.stop()             # arrête le polling
    """
    
    def __init__(self, token: str, chat_id: str, agent=None):
        self.token = token
        self.chat_id = chat_id
        self.agent = agent
        self._app: Application | None = None
        self._thread: threading.Thread | None = None
        self._started = False          # ← garde anti-double démarrage
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Démarrage / Arrêt ────────────────────────────────────

    def start(self) -> None:
        if self._started:
            logger.warning("Telegram déjà démarré, appel ignoré")
            return
        if not self.token or not self.chat_id:
            logger.info("Telegram désactivé : token ou chat_id manquant")
            return
        try:
            self._app = Application.builder().token(self.token).build()
            # Nettoyer les updates en attente pour éviter le conflit getUpdates
            bot = Bot(self.token)
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                updates = loop.run_until_complete(bot.get_updates(timeout=1))
                if updates:
                    logger.info("Telegram : %d update(s) en attente nettoyée(s)", len(updates))
            except Exception:
                pass
            finally:
                loop.close()
            self._register_handlers()
            self._started = True
            # Thread daemon = s'arrête automatiquement si le process principal meurt
            self._thread = threading.Thread(target=self._poll_forever, daemon=True)
            self._thread.start()
            logger.info("Telegram bot démarré")
        except Exception as e:
            self._started = False
            logger.warning("Impossible de démarrer Telegram : %s", e)

    def stop(self) -> None:
        if not self._app or not self._started:
            return
        self._started = False
        try:
            if self._loop and self._loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self._app.updater.stop(), self._loop
                )
                future.result(timeout=5)
        except Exception as e:
            logger.warning("Erreur à l'arrêt Telegram : %s", e)
        finally:
            self._app = None
            if self._thread:
                self._thread.join(timeout=5)
                self._thread = None
            logger.info("Telegram bot arrêté")

    def _poll_forever(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._app.run_polling(allowed_updates=Update.ALL_TYPES)

    # ── Commandes ────────────────────────────────────────────

    def _register_handlers(self) -> None:
        if not self._app:
            return
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("pnl", self._cmd_pnl))
        self._app.add_handler(CommandHandler("perf", self._cmd_perf))
        self._app.add_handler(CommandHandler("config", self._cmd_config))
        self._app.add_handler(CommandHandler("history", self._cmd_history))
        self._app.add_handler(CommandHandler("close", self._cmd_close))
        self._app.add_handler(CommandHandler("closeall", self._cmd_closeall))
        self._app.add_handler(CommandHandler("chart", self._cmd_chart))
        self._app.add_handler(CommandHandler("signals", self._cmd_signals))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))
        self._app.add_handler(CommandHandler("set", self._cmd_set))

    async def _reply(self, update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(text, parse_mode="HTML")

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, (
            "🤖 <b>TradingBotCDC</b>\n\n"
            "Bot de trading paper/live Crypto.com\n"
            "Stratégie : Momentum + Sentiment + Fear & Greed\n\n"
            "Envoie /help pour voir toutes les commandes disponibles."
        ))

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, (
            "🤖 <b>TradingBotCDC — Aide complète</b>\n\n"
            "📊 <b>Informations</b>\n"
            "/status — État général du bot\n"
            "/positions — Positions ouvertes\n"
            "/pnl — P&L détaillé\n"
            "/perf — Statistiques de performance\n"
            "/config — Configuration active\n"
            "/history [N] — Derniers N trades (défaut: 5)\n\n"
            "📈 <b>Graphiques</b>\n"
            "/chart — Graphique de performance\n"
            "/signals — Graphique des signaux\n\n"
            "🎯 <b>Actions</b>\n"
            "/close SYM — Fermer une position (ex: /close BTC)\n"
            "/closeall — Fermer toutes les positions\n"
            "/pause — Mettre le bot en pause\n"
            "/resume — Reprendre le trading\n"
            "/set SYM TP SL — Modifier TP/SL d'une position\n\n"
            "💡 <b>Exemples</b>\n"
            "/close BTC → ferme la position BTC\n"
            "/set ETH 8 4 → TP 8% / SL 4% sur ETH\n"
            "/history 10 → 10 derniers trades"
        ))

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        try:
            agent = self.agent
            marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
            equity, unreal = agent.paper.equity(marks)
            pos_count = sum(1 for p in agent.paper.positions.values() if p.qty != 0)
            cash = agent.paper.cash
            realized = agent.paper.realized_pnl
            perf = (equity / agent.initial_capital - 1) * 100
            pause_status = "⏸️ PAUSÉ" if agent._paused else "▶️ ACTIF"
            night = "🌙 Night" if agent._is_night_trade() else "☀️ Day"
            closed_count = sum(1 for t in agent.paper.trades if t.get("pnl", 0) != 0)
            msg = (
                f"📊 <b>État du bot</b>  {pause_status}  {night}\n"
                f"├ Mode : {agent.mode}\n"
                f"├ Universe : {len(agent.s.universe)} actifs\n"
                f"├ Cash : ${cash:.2f}\n"
                f"├ Equity : ${equity:.2f} ({perf:+.2f}%)\n"
                f"├ P&L Réalisé : ${realized:+.2f}\n"
                f"├ P&L Non-réalisé : ${unreal:+.2f}\n"
                f"├ Positions : {pos_count}/{agent.max_concurrent_positions}\n"
                f"└ Trades fermés : {closed_count}"
            )
            await self._reply(update, msg)
        except Exception as e:
            await self._reply(update, f"❌ Erreur : {e}")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        agent = self.agent
        positions = [(s, p) for s, p in agent.paper.positions.items() if p.qty != 0]
        if not positions:
            await self._reply(update, "📭 Aucune position ouverte")
            return
        lines = ["📈 <b>Positions ouvertes</b>"]
        marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
        total_unreal = 0.0
        for sym, pos in positions:
            mp = marks.get(sym, pos.avg_price)
            if pos.side == "buy":
                pnl = (mp - pos.avg_price) * pos.qty
            else:
                pnl = (pos.avg_price - mp) * abs(pos.qty)
            total_unreal += pnl
            pnl_pct = (mp / pos.avg_price - 1) * 100 * (1 if pos.side == "buy" else -1)
            emoji = "🟢" if pnl >= 0 else "🔴"
            side_emoji = "📗 LONG" if pos.side == "buy" else "📕 SHORT"
            lines.append(
                f"{emoji} <b>{sym.split('-')[0]}</b> {side_emoji}\n"
                f"├ Entrée : ${pos.avg_price:.4f}\n"
                f"├ Actuel : ${mp:.4f}\n"
                f"├ Qty : {abs(pos.qty):.4f}\n"
                f"├ P&L : ${pnl:+.2f} ({pnl_pct:+.2f}%)\n"
                f"└ SL {agent.stop_loss_pct*100:.0f}% / TP {agent.take_profit_pct*100:.0f}%"
            )
        lines.append(f"\n💸 <b>Total non-réalisé : ${total_unreal:+.2f}</b>")
        await self._reply(update, "\n".join(lines))

    async def _cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        agent = self.agent
        marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
        equity, unreal = agent.paper.equity(marks)
        initial = agent.initial_capital
        perf = (equity / initial - 1) * 100
        closed_count = sum(1 for t in agent.paper.trades if t.get("pnl", 0) != 0)
        msg = (
            f"💰 <b>P&L</b>\n"
            f"├ Capital initial : ${initial:.2f}\n"
            f"├ Equity : ${equity:.2f}\n"
            f"├ Performance : {perf:+.3f}%\n"
            f"├ P&L Réalisé : ${agent.paper.realized_pnl:+.2f}\n"
            f"├ P&L Non-réalisé : ${unreal:+.2f}\n"
            f"├ Trades fermés : {closed_count}\n"
            f"└ Win Rate : {self._win_rate()*100:.1f}%"
        )
        await self._reply(update, msg)

    async def _cmd_perf(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        agent = self.agent
        marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
        equity, _ = agent.paper.equity(marks)
        perf = (equity / agent.initial_capital - 1) * 100
        wr = self._win_rate() * 100
        dd = self._max_dd() * 100
        sharpe = self._sharpe()
        closed = [t for t in agent.paper.trades if t.get("pnl", 0) != 0]
        trades = len(closed)
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        losses = trades - wins
        best = max((t.get("pnl", 0) for t in closed), default=0)
        worst = min((t.get("pnl", 0) for t in closed), default=0)
        msg = (
            f"📊 <b>Statistiques de performance</b>\n"
            f"├ Performance : {perf:+.2f}%\n"
            f"├ Win Rate : {wr:.1f}%\n"
            f"├ Trades : {trades} (✅{wins} ❌{losses})\n"
            f"├ Best Trade : ${best:+.2f}\n"
            f"├ Worst Trade : ${worst:+.2f}\n"
            f"├ Max Drawdown : {dd:.2f}%\n"
            f"└ Sharpe Ratio : {sharpe:.2f}"
        )
        await self._reply(update, msg)

    async def _cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        agent = self.agent
        cfg = agent.strategy.cfg
        universe_str = ", ".join(s.split("-")[0] for s in agent.s.universe)
        msg = (
            "⚙️ <b>Configuration active</b>\n\n"
            "📌 <b>Stratégie</b>\n"
            f"├ Poids Momentum : {cfg.w_momentum:.0%}\n"
            f"├ Poids Sentiment : {cfg.w_sentiment:.0%}\n"
            f"├ Poids Fear & Greed : {cfg.w_fear_greed:.0%}\n"
            f"├ Lookback : {cfg.lookback} périodes\n"
            f"├ EMA smooth : {cfg.ema_smooth}\n\n"
            "🎯 <b>Seuils</b>\n"
            f"├ LONG sup= {cfg.threshold_long:+.2f}\n"
            f"├ SHORT inf= {cfg.threshold_short:+.2f}\n"
            f"├ Fermeture LONG inf {cfg.close_threshold:+.2f}\n"
            f"├ Fermeture SHORT sup {cfg.close_short_threshold:+.2f}\n"
            f"├ Short autorisé : {'Oui' if cfg.allow_short else 'Non'}\n\n"
            "🛡️ <b>Risk Management</b>\n"
            f"├ Capital : ${agent.initial_capital:.0f}\n"
            f"├ Max/trade : ${agent.max_position_usd:.0f}\n"
            f"├ Max positions : {agent.max_concurrent_positions}\n"
            f"├ TP : {agent.take_profit_pct*100:.0f}% / SL : {agent.stop_loss_pct*100:.0f}%\n"
            f"├ Equity SL : {agent.equity_stop_loss_pct*100:.0f}%\n\n"
            f"🌐 <b>Universe ({len(agent.s.universe)} actifs)</b>\n"
            f"└ {universe_str}"
        )
        await self._reply(update, msg)

    async def _cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        try:
            limit = 5
            if context.args and context.args[0].isdigit():
                limit = min(int(context.args[0]), 50)
            trades = self.agent.paper.trades[-limit:] if self.agent.paper.trades else []
            if not trades:
                await self._reply(update, "📭 Aucun trade enregistré")
                return
            lines = [f"📜 <b>Derniers {len(trades)} trades</b>"]
            for t in reversed(trades):
                emoji = "🟢" if t.get("pnl", 0) > 0 else "🔴" if t.get("pnl", 0) < 0 else "⚪"
                side = t.get("side", "").upper()
                sym = t.get("symbol", "?")
                qty = t.get("qty", 0)
                price = t.get("price", 0)
                pnl = t.get("pnl", 0)
                fee = t.get("fee", 0)
                pnl_str = f" | P&L ${pnl:+.2f}" if pnl != 0 else ""
                lines.append(
                    f"{emoji} <b>{sym}</b> {side}\n"
                    f"└ {qty:.4f} @ ${price:.2f}{pnl_str} (fee ${fee:.4f})"
                )
            await self._reply(update, "\n\n".join(lines))
        except Exception as e:
            await self._reply(update, f"❌ Erreur : {e}")

    async def _cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        if not context.args:
            await self._reply(update, "ℹ️ Utilisation : /close SYM (ex: /close BTC)\n"
                                      "Astuce : /closeall pour tout fermer")
            return
        sym_raw = context.args[0].upper()
        full_sym = None
        for s in self.agent.s.universe:
            if s.startswith(sym_raw):
                full_sym = s
                break
        if not full_sym:
            await self._reply(update, f"❌ Symbole {sym_raw} introuvable dans l'univers")
            return
        pos = self.agent.paper.positions.get(full_sym)
        if not pos or pos.qty == 0:
            await self._reply(update, f"📭 Pas de position sur {full_sym}")
            return
        side = "sell" if pos.side == "buy" else "buy"
        price = self.agent._last_snapshots.get(full_sym, {}).get("price", 0)
        if price <= 0:
            await self._reply(update, "❌ Prix non disponible pour la clôture")
            return
        tr = self.agent._execute(full_sym, side, abs(pos.qty), price)
        await self._reply(update,
            f"✅ <b>Position fermée</b>\n"
            f"├ {full_sym} {pos.qty:.4f} @ ${price:.2f}\n"
            f"└ P&L : ${tr.get('pnl', 0):+.2f}"
        )

    async def _cmd_closeall(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        agent = self.agent
        marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
        closed = 0
        with agent._broker_lock:
            for sym, p in list(agent.paper.positions.items()):
                if p.qty != 0:
                    close_side = "sell" if p.side == "buy" else "buy"
                    price = marks.get(sym, p.avg_price)
                    if price > 0:
                        agent._execute(sym, close_side, abs(p.qty), price)
                        closed += 1
        await self._reply(update,
            f"✅ <b>Fermeture en masse</b>\n"
            f"└ {closed} position(s) fermée(s)"
        )

    async def _cmd_chart(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        try:
            from .telegram_chart import equity_chart
            closed = [t for t in self.agent.paper.trades if t.get("pnl", 0) != 0]
            buf = equity_chart(
                equity_history=self.agent._equity_history,
                initial_capital=self.agent.initial_capital,
                trades_count=len(closed),
                win_rate=self._win_rate(),
                max_dd=self._max_dd(),
                sharpe=self._sharpe(),
            )
            chat_id = self._resolve_chat_id()
            bot = Bot(self.token)
            await bot.send_photo(chat_id=chat_id, photo=buf, caption="📊 Performance du bot")
        except Exception as e:
            await self._reply(update, f"❌ Erreur graphique : {e}")

    async def _cmd_signals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        try:
            from .telegram_chart import signals_chart
            scores = {s: (d.get("score") or 0) for s, d in self.agent._last_snapshots.items()}
            buf = signals_chart(
                scores=scores,
                threshold_long=self.agent.strategy.cfg.threshold_long,
                threshold_short=self.agent.strategy.cfg.threshold_short,
            )
            if buf is None:
                await self._reply(update, "📭 Aucun signal disponible")
                return
            chat_id = self._resolve_chat_id()
            bot = Bot(self.token)
            await bot.send_photo(chat_id=chat_id, photo=buf, caption="📡 Signaux en direct")
        except Exception as e:
            await self._reply(update, f"❌ Erreur graphique : {e}")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        self.agent._paused = True
        await self._reply(update, "⏸️ <b>Bot en pause</b>\n"
                                  "Les nouveaux trades sont désactivés.\n"
                                  "Les positions existantes sont toujours gérées (SL/TP).\n"
                                  "Utilise /resume pour reprendre.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        self.agent._paused = False
        await self._reply(update, "▶️ <b>Bot repris</b>\n"
                                  "Les nouveaux trades sont réactivés.")

    async def _cmd_set(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        if len(context.args) < 3:
            await self._reply(update, "ℹ️ Utilisation : /set SYM TP% SL%\n"
                                      "Exemple : /set ETH 8 4 → TP 8% / SL 4%")
            return
        sym_raw = context.args[0].upper()
        full_sym = None
        for s in self.agent.s.universe:
            if s.startswith(sym_raw):
                full_sym = s
                break
        if not full_sym:
            await self._reply(update, f"❌ Symbole {sym_raw} introuvable")
            return
        pos = self.agent.paper.positions.get(full_sym)
        if not pos or pos.qty == 0:
            await self._reply(update, f"📭 Pas de position sur {full_sym}")
            return
        try:
            new_tp = float(context.args[1]) / 100
            new_sl = float(context.args[2]) / 100
        except ValueError:
            await self._reply(update, "❌ TP et SL doivent être des nombres (ex: /set ETH 8 4)")
            return
        if pos.side == "buy":
            self.agent.take_profit_pct = new_tp
            self.agent.stop_loss_pct = new_sl
        else:
            self.agent.short_take_profit_pct = new_tp
            self.agent.short_stop_loss_pct = new_sl
        await self._reply(update,
            f"✅ <b>TP/SL mis à jour pour {full_sym}</b>\n"
            f"├ TP : {new_tp*100:.0f}%\n"
            f"└ SL : {new_sl*100:.0f}%"
        )

    def _win_rate(self) -> float:
        if not self.agent:
            return 0.0
        # Ne compter que les trades fermés (pnl != 0), pas les ouvertures (pnl == 0)
        closed = [t for t in self.agent.paper.trades if t.get("pnl", 0) != 0]
        if not closed:
            return 0.0
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        return wins / len(closed)

    def _max_dd(self) -> float:
        if not self.agent:
            return 0.0
        eq = pd.Series(self.agent._equity_history)
        if eq.empty:
            return 0.0
        peak = eq.cummax()
        dd = (eq - peak) / peak
        return dd.min()

    def _sharpe(self) -> float:
        if not self.agent:
            return 0.0
        eq = pd.Series(self.agent._equity_history)
        if len(eq) < 2:
            return 0.0
        rets = eq.pct_change().dropna()
        if rets.std() == 0:
            return 0.0
        return float((rets.mean() / rets.std()) * (365 * 24) ** 0.5)

    # ── Notifications ────────────────────────────────────────

    def _resolve_chat_id(self) -> int | str:
        cid = self.chat_id.strip()
        if cid.lstrip('-').isdigit():
            return int(cid)
        if cid.startswith('@'):
            return cid[1:]
        if 't.me/' in cid:
            return cid.split('t.me/')[-1].split()[0].strip()
        return cid

    async def send(self, message: str) -> None:
        if not self.token or not self.chat_id:
            return
        try:
            chat_id = self._resolve_chat_id()
            bot = Bot(self.token)
            await bot.send_message(chat_id=chat_id, text=message, parse_mode="HTML")
        except telegram.error.ChatMigrated as e:
            logger.warning("Telegram : chat migré vers %s, mise à jour nécessaire", e.new_chat_id)
        except telegram.error.BadRequest as e:
            err = str(e).lower()
            if "chat not found" in err:
                logger.error(
                    "❌ Telegram : impossible de trouver le chat \"%s\".\n"
                    "  Pour un chat_id numérique :\n"
                    "   1. Envoie /start au bot depuis Telegram\n"
                    "   2. Va voir @userinfobot → il te donnera ton ID\n"
                    "   3. Mets cet ID numérique dans .env\n\n"
                    "  Pour un @username :\n"
                    "   - Le bot doit avoir reçu un message de ce chat\n"
                    "   - Utilise @username (sans le t.me/)\n\n"
                    "  Exemple correct : TELEGRAM_CHAT_ID=1234567890",
                    self.chat_id
                )
            else:
                logger.warning("Telegram BadRequest : %s", e)
        except Exception as e:
            logger.warning("Échec envoi Telegram : %s", e)

    def send_sync(self, message: str) -> None:
        if not self.token or not self.chat_id:
            return
        try:
            loop = self._loop or asyncio.new_event_loop()
            coro = self.send(message)
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(coro, loop)
            else:
                loop.run_until_complete(coro)
        except Exception as e:
            logger.warning("Échec envoi Telegram sync : %s", e)