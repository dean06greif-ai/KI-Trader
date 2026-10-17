"""Wöchentlicher Copilot-Report per Telegram.

Der Strategie-Copilot analysiert einmal pro Woche die vorhandenen Strategien
(Indikatoren + echte Ergebnisse) und den KI-Setup-Katalog: Was sollte optimiert
werden, warum, und welche sinnvollen Setups fehlen. Das Ergebnis geht als
Telegram-Nachricht raus (Toggle "copilot_weekly" in den Notification-Settings).
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

SETTINGS_ID = "copilot_weekly"
DEFAULTS = {"enabled": False, "weekday": 0, "hour": 9}  # Montag 09:00 Berlin
CHECK_INTERVAL_S = 900
MIN_GAP_HOURS = 72  # nie öfter als alle 3 Tage (Schutz vor Doppel-Sends)
BERLIN = ZoneInfo("Europe/Berlin")

REPORT_SYSTEM = (
    "Du bist der Strategie-Copilot einer Krypto-Daytrading-Plattform. Du bist "
    "Berater: Du entwickelst keine Strategien, du analysierst und empfiehlst. "
    "Antworte auf Deutsch als kompakter Telegram-Report (max. ~300 Wörter), "
    "Klartext mit kurzen Zeilen/Aufzählungen, KEINE Tabellen, kein JSON.")

REPORT_PROMPT = (
    "Erstelle den wöchentlichen Setup-Report:\n"
    "1) Welche 2-3 vorhandenen Strategien sollte ich diese Woche optimieren und "
    "warum (Zahlen zitieren)?\n"
    "2) Welche sinnvollen Setups/Indikator-Kombinationen FEHLEN derzeit "
    "(Lücken in der Abdeckung: Marktphasen, Timeframes, Mean-Reversion vs. Trend)?\n"
    "3) Ein konkreter nächster Schritt im Strategie-Labor (Modus + Einstellungen).\n"
    "Erfinde keine Zahlen – nutze nur die Daten unten.")


async def get_config(db) -> Dict:
    doc = {}
    try:
        doc = await db.settings.find_one({"_id": SETTINGS_ID}) or {}
    except Exception as e:
        logger.warning(f"Copilot-Weekly: Config nicht lesbar: {e}")
    cfg = dict(DEFAULTS)
    for k in DEFAULTS:
        if k in doc:
            cfg[k] = doc[k]
    cfg["last_sent"] = doc.get("last_sent")
    cfg["last_error"] = doc.get("last_error")
    return cfg


async def set_config(db, updates: Dict) -> Dict:
    clean: Dict = {}
    if "enabled" in updates:
        clean["enabled"] = bool(updates["enabled"])
    if "weekday" in updates:
        clean["weekday"] = max(0, min(6, int(updates["weekday"])))
    if "hour" in updates:
        clean["hour"] = max(0, min(23, int(updates["hour"])))
    if clean:
        await db.settings.update_one({"_id": SETTINGS_ID}, {"$set": clean}, upsert=True)
    return await get_config(db)


def is_due(cfg: Dict, now_berlin: datetime, last_sent_iso: Optional[str]) -> bool:
    """Rein & testbar: ist der Wochen-Report jetzt fällig?"""
    if not cfg.get("enabled"):
        return False
    if now_berlin.weekday() != int(cfg.get("weekday", 0)):
        return False
    if now_berlin.hour < int(cfg.get("hour", 9)):
        return False
    if last_sent_iso:
        try:
            last = datetime.fromisoformat(str(last_sent_iso))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600.0
            if age_h < MIN_GAP_HOURS:
                return False
        except (TypeError, ValueError):
            pass
    return True


async def _context_text(db) -> str:
    from services.strategy_copilot import copilot
    parts = []
    try:
        ov = await copilot._strategies_overview()
        if ov:
            parts.append("STRATEGIE-ÜBERSICHT (Indikatoren + echte Ergebnisse):\n" + ov)
    except Exception as e:
        logger.warning(f"Copilot-Weekly: Strategie-Übersicht fehlt: {e}")
    try:
        from services import ai_playbook
        stats = await ai_playbook.setup_stats(db)
        lines = []
        for sid, desc in ai_playbook.SETUPS.items():
            st = stats.get(sid) or {}
            perf = (f"{st.get('trades')}T · {st.get('wins')}W · PnL {st.get('pnl'):+.2f}$ "
                    f"({st.get('verdict')})" if st else "keine Daten")
            lines.append(f"- {sid}: {perf}")
        parts.append("KI-SETUP-KATALOG (echte KI-Trade-Ergebnisse):\n" + "\n".join(lines))
    except Exception as e:
        logger.warning(f"Copilot-Weekly: Setup-Katalog fehlt: {e}")
    return "\n\n".join(parts) or "(keine Daten verfügbar)"


async def send_report(db, trigger: str = "schedule") -> Dict:
    from services.strategy_copilot import copilot
    ctx = await _context_text(db)
    text = await copilot.freeform(f"{REPORT_PROMPT}\n\nDATEN:\n{ctx}",
                                  system=REPORT_SYSTEM)
    sent = False
    try:
        from core import state
        from services import notifications
        sent = await notifications.telegram_notify(
            db, state.telegram, "copilot_weekly",
            "🧭 *COPILOT WOCHEN-REPORT* (Setups & Optimierung)\n\n" + text[:3500])
    except Exception as e:
        logger.warning(f"Copilot-Weekly: Telegram-Versand fehlgeschlagen: {e}")
    now = datetime.now(timezone.utc).isoformat()
    await db.settings.update_one(
        {"_id": SETTINGS_ID},
        {"$set": {"last_sent": now, "last_error": None, "last_trigger": trigger}},
        upsert=True)
    return {"sent": bool(sent), "report": text, "sent_at": now, "trigger": trigger}


async def weekly_loop(db):
    """Hintergrund-Loop: prüft alle 15 min, ob der Wochen-Report fällig ist."""
    await asyncio.sleep(120)  # Boot abwarten
    while True:
        try:
            cfg = await get_config(db)
            if is_due(cfg, datetime.now(BERLIN), cfg.get("last_sent")):
                logger.info("Copilot-Weekly: Report fällig – wird erstellt")
                await send_report(db, trigger="schedule")
        except Exception as e:
            logger.warning(f"Copilot-Weekly-Loop: {e}")
            try:
                await db.settings.update_one(
                    {"_id": SETTINGS_ID}, {"$set": {"last_error": str(e)[:300]}}, upsert=True)
            except Exception:
                pass
        await asyncio.sleep(CHECK_INTERVAL_S)
