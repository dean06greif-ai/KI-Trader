import os
from telegram import Bot
from telegram.constants import ParseMode
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Send trading signals via Telegram"""
    
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.frontend_url = os.getenv('FRONTEND_URL', 'https://crypto-scanner-frontend-a98r.onrender.com')
        self.bot = None
        
        if self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
                logger.info("Telegram bot initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram bot: {e}")
    
    @staticmethod
    def rules_counts(signal: Dict):
        """(erfüllt, gesamt) aus dem Signal – Bugfix 09/2026: vorher stand
        fest "/4" in der Nachricht, Custom-Strategien mit 3 Regeln je Richtung
        (z.B. TrendFolge2) zeigten dadurch irreführend "3/4"."""
        total = signal.get('rules_total')
        if not total:
            rm = signal.get('rules_met')
            total = len(rm) if isinstance(rm, dict) and rm else 4
        met = signal.get('rules_met_count')
        if met is None:
            rm = signal.get('rules_met')
            met = sum(1 for v in rm.values() if v) if isinstance(rm, dict) else total
        return int(met), int(total)

    def format_signal_message(self, signal: Dict) -> str:
        """Format signal data into Telegram message with better SL/TP context and website link"""
        signal_type = signal['type']
        is_pre_signal = signal.get('signal_class') == 'PRE_SIGNAL'
        strategy_name = signal.get('strategy_name', 'Scalping')
        
        if is_pre_signal:
            emoji = "🟡"
            title = f"⚠️ *PRE-{signal_type} WARNING*"
            action = "🔔 *Trade vorbereiten - 4. Regel steht bevor!*"
        else:
            emoji = "🟢" if signal_type == "LONG" else "🔴"
            title = f"{emoji} *{signal_type} SIGNAL* {emoji}"
            action = "🎯 *ACTION: Enter trade within 2 candles!*"
        
        # BUGFIX: Signale ohne Entry/SL/TP (None-Werte) führten zu
        # "unsupported operand type(s) for -: 'NoneType' and 'NoneType'" –
        # die Nachricht ging dann nie raus. Jetzt None-sicher formatieren.
        entry = signal.get('entry_price')
        sl = signal.get('stop_loss')
        tp1 = signal.get('take_profit_1')
        tp_full = signal.get('take_profit_full')

        def _pct(level):
            try:
                e, lv = float(entry), float(level)
                return abs((lv - e) / e * 100) if e else None
            except (TypeError, ValueError):
                return None

        def _line(emoji_lbl, label, level, sign):
            if level is None:
                return f"{emoji_lbl} *{label}:* n/a"
            p = _pct(level)
            return f"{emoji_lbl} *{label}:* `${level}`" + \
                (f" ({sign}{p:.2f}%)" if p is not None else "")

        # Dynamic partial-close percent from the signal (falls back to 50%,
        # which is what DEFAULT_COIN_CFG.tp1_close_percent uses now).
        tp1_close_pct = int(signal.get('tp1_close_percent') or 50)
        rules_met, rules_total = self.rules_counts(signal)
        info = signal.get('_trade_info') or {}
        trade_lines = ""
        if info:
            action = "✅ *Trade automatisch eröffnet*"
            trade_lines = f"🏷 *Trade:* {info.get('mode_label')}\n"
            if info.get('setup'):
                trade_lines += f"🧩 *Setup:* `{info['setup']}`\n"

        message = f"""{title}

💰 *{signal['symbol']}* · {strategy_name}
🕐 Session: {signal.get('session', 'N/A')} | Rules: {rules_met}/{rules_total}
{trade_lines}
━━━━━━━━━━━━━━━━━━━
💵 *ENTRY:* `${entry if entry is not None else 'n/a'}`
{_line('🛑', 'STOP LOSS', sl, '-')}
{_line('🎯', f'TP1 ({tp1_close_pct}%)', tp1, '+')}
{_line('🚀', 'TP FULL', tp_full, '+')}
━━━━━━━━━━━━━━━━━━━

📊 *CRV:* {signal.get('crv', 'n/a')} | RSI: {signal.get('rsi', 'n/a')}

{action}

🔗 [Open Live Dashboard]({self.frontend_url})
"""
        return message
    
    async def send_signal(self, signal: Dict) -> bool:
        """Send signal notification via Telegram"""
        if not self.bot or not self.chat_id:
            logger.warning("Telegram not configured, skipping notification")
            return False
        
        try:
            message = self.format_signal_message(signal)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            
            logger.info(f"Telegram notification sent for {signal['symbol']} {signal['type']}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False
    
    async def send_rejection(self, symbol: str, side: str, reason: str,
                             internal: bool = False) -> bool:
        """Notify that a live order was rejected by Bitunix and the trade
        was NOT opened locally (no ghost position)."""
        if not self.bot or not self.chat_id:
            logger.warning("Telegram not configured, skipping rejection alert")
            return False
        try:
            message = (
                f"⛔ *ORDER ABGEBROCHEN*\n\n"
                f"💰 *{symbol}* · {side}\n"
                + ("🛑 Interne Prüfung hat die Order gestoppt:\n" if internal
                   else "❌ Bitunix hat die Order abgelehnt:\n") +
                f"`{reason}`\n\n"
                f"⚠️ Es wurde *kein* Trade lokal geöffnet."
            )
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send rejection notification: {e}")
            return False

    async def send_test_message(self) -> bool:
        """Send test message to verify bot setup"""
        if not self.bot or not self.chat_id:
            return False
        
        try:
            message = f"""✅ *Crypto Scanner Bot Connected!*

Bot ist bereit und wartet auf Signale.

🔗 [Open Dashboard]({self.frontend_url})
"""
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send test message: {e}")
            return False
