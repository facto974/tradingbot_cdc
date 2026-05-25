"""Bot Telegram — notifications trades + commandes en direct."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import telegram
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

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
        self.agent = agent  # référence au TradingAgent pour les commandes
        self._app: Application | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── Démarrage / Arrêt ────────────────────────────────────

    def start(self) -> None:
        """Démarre le polling Telegram en arrière-plan."""
        if not self.token or not self.chat_id:
            logger.info("Telegram désactivé : token ou chat_id manquant")
            return
        try:
            self._app = Application.builder().token(self.token).build()
            self._register_handlers()
            self._loop = asyncio.new_event_loop()
            self._loop.run_in_executor(None, self._poll_forever)
            logger.info("Telegram bot démarré")
        except Exception as e:
            logger.warning("Impossible de démarrer Telegram : %s", e)

    def stop(self) -> None:
        """Arrête le polling."""
        if self._app:
            try:
                if self._loop and self._loop.is_running():
                    self._loop.call_soon_threadsafe(self._app.stop)
                else:
                    asyncio.run_coroutine_threadsafe(
                        self._app.stop(), self._loop
                    ) if self._loop else None
            except Exception:
                pass
            self._app = None
            logger.info("Telegram bot arrêté")

    def _poll_forever(self) -> None:
        """Boucle polling exécutée dans un thread dédié."""
        asyncio.set_event_loop(self._loop)
        self._app.run_polling(allowed_updates=Update.ALL_TYPES)

    # ── Commandes ────────────────────────────────────────────

    def _register_handlers(self) -> None:
        if not self._app:
            return
        self._app.add_handler(CommandHandler("start", self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("positions", self._cmd_positions))
        self._app.add_handler(CommandHandler("pnl", self._cmd_pnl))
        self._app.add_handler(CommandHandler("close", self._cmd_close))

    async def _reply(self, update: Update, text: str) -> None:
        if update.effective_message:
            await update.effective_message.reply_text(text, parse_mode="HTML")

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await self._reply(update, (
            "🤖 <b>GeminiMCP Trader</b>\n\n"
            "Commandes disponibles :\n"
            "/status  — État général du bot\n"
            "/positions — Positions ouvertes\n"
            "/pnl     — P&L et equity\n"
            "/close SYM — Fermer la position (ex: /close BTC)\n"
        ))

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        try:
            agent = self.agent
            marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
            equity, unreal = agent.paper.equity(marks)
            pos_count = sum(1 for p in agent.paper.positions.values() if p.qty > 0)
            cash = agent.paper.cash
            realized = agent.paper.realized_pnl
            msg = (
                f"📊 <b>État du bot</b>\n"
                f"├ Mode : {agent.mode}\n"
                f"├ Universe : {len(agent.s.universe)} actifs\n"
                f"├ Cash : ${cash:.2f}\n"
                f"├ Equity : ${equity:.2f}\n"
                f"├ P&L Réel : ${realized:+.2f}\n"
                f"├ P&L Non-réel : ${unreal:+.2f}\n"
                f"├ Positions : {pos_count}\n"
                f"└ Trades : {len(agent.paper.trades)}"
            )
            await self._reply(update, msg)
        except Exception as e:
            await self._reply(update, f"❌ Erreur : {e}")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        agent = self.agent
        positions = [(s, p) for s, p in agent.paper.positions.items() if p.qty > 0]
        if not positions:
            await self._reply(update, "📭 Aucune position ouverte")
            return
        lines = ["📈 <b>Positions ouvertes</b>"]
        marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
        for sym, pos in positions:
            mp = marks.get(sym, pos.avg_price)
            pnl = (mp - pos.avg_price) * pos.qty
            emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{emoji} <b>{sym.split('-')[0]}</b>  "
                f"{pos.qty:.4f} @ ${pos.avg_price:.2f}  "
                f"→ ${mp:.2f}  ({pnl:+.2f})"
            )
        await self._reply(update, "\n".join(lines))

    async def _cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        agent = self.agent
        marks = {s: d.get("price", 0) for s, d in agent._last_snapshots.items()}
        equity, unreal = agent.paper.equity(marks)
        initial = 10000.0
        perf = (equity / initial - 1) * 100
        msg = (
            f"💰 <b>P&L</b>\n"
            f"├ Capital initial : ${initial:.2f}\n"
            f"├ Equity : ${equity:.2f}\n"
            f"├ Performance : {perf:+.3f}%\n"
            f"├ P&L Réalisé : ${agent.paper.realized_pnl:+.2f}\n"
            f"└ P&L Non-réalisé : ${unreal:+.2f}"
        )
        await self._reply(update, msg)

    async def _cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.agent:
            await self._reply(update, "❌ Agent non connecté")
            return
        if not context.args:
            await self._reply(update, "ℹ️ Utilisation : /close SYM (ex: /close BTC)")
            return
        sym_raw = context.args[0].upper()
        # Trouver le symbole complet (BTC-USD, ETH-USD, etc.)
        full_sym = None
        for s in self.agent.s.universe:
            if s.startswith(sym_raw):
                full_sym = s
                break
        if not full_sym:
            await self._reply(update, f"❌ Symbole {sym_raw} introuvable dans l'universe")
            return
        pos = self.agent.paper.positions.get(full_sym)
        if not pos or pos.qty == 0:
            await self._reply(update, f"📭 Pas de position sur {full_sym}")
            return
        # Fermer la position
        side = "sell" if pos.side == "buy" else "buy"
        price = self.agent._last_snapshots.get(full_sym, {}).get("price", 0)
        if price <= 0:
            await self._reply(update, "❌ Prix non disponible pour la clôture")
            return
        tr = self.agent._execute(full_sym, side, pos.qty, price)
        pnl_str = f" P&L={tr['pnl']:+.2f}" if tr.get("pnl") else ""
        await self._reply(update,
            f"✅ <b>Position fermée</b>\n"
            f"├ {full_sym} {pos.qty:.4f} @ ${price:.2f}\n"
            f"└ P&L : ${tr.get('pnl', 0):+.2f}"
        )

    # ── Notifications ────────────────────────────────────────

    def _resolve_chat_id(self) -> int | str:
        """Convertit chat_id au format attendu par l'API Telegram."""
        cid = self.chat_id.strip()
        # Si c'est un nombre (ex: 1234567890 ou -1234567890)
        if cid.lstrip('-').isdigit():
            return int(cid)
        # Si c'est un @username, le passer tel quel (sans @)
        if cid.startswith('@'):
            return cid[1:]
        # Si c'est un lien t.me/username, extraire le username
        if 't.me/' in cid:
            return cid.split('t.me/')[-1].split()[0].strip()
        # Sinon, le passer tel quel (sera rejeté par l'API)
        return cid

    async def send(self, message: str) -> None:
        """Envoie une notification asynchrone."""
        if not self.token or not self.chat_id:
            return
        try:
            chat_id = self._resolve_chat_id()
            bot = Bot(self.token)
            await bot.send_message(chat_id=chat_id, text=message,
                                   parse_mode="HTML")
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
        """Version synchrone pour appeler depuis le thread principal."""
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