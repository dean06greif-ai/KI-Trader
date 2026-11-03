"""Wöchentlicher Edge-Bericht per Telegram (deterministisch, kein LLM – keine KI-Kosten).

Fasst die letzten 7 Tage des KI-Traders in R zusammen und meldet den Stand
des Live-Freigabe-Checks (services/edge_filter.readiness). Zeitplan und
Doppel-Send-Schutz wie copilot_weekly (Toggle „edge_weekly“ in den
Benachrichtigungen, Zeitplan in settings._id=edge_weekly).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from services.copilot_weekly import BERLIN, is_due

logger = logging.getLogger(__name__)

SETTINGS_ID = "edge_weekly"
DEFAULTS = {"enabled": True, "weekday": 6, "hour": 19}  # Sonntag 19:00 Berlin
CHECK_INTERVAL_S = 900


def _r(v: Optional[float]) -> str:
    return "–" if v is None else f"{v:+.2f} R"


def format_report(week: Dict, ready: Dict, blocks: Dict) -> str:
    """Rein: Telegram-Text aus Edge-Bericht-Teilen."""
    ov = week.get("overall") or {}
    lines = ["📊 *KI-TRADER WOCHEN-EDGE* (letzte 7 Tage)", ""]
    if not ov.get("with_risk"):
        lines.append("Keine geschlossenen KI-Trades in den letzten 7 Tagen.")
    else:
        lines.append(f"Trades: {ov['with_risk']} · netto {_r(ov.get('exp_r'))} · brutto {_r(ov.get('gross_r'))} "
                     f"· Gebühren {ov.get('fee_r', 0):.2f} R")
        lines.append(f"PnL: {ov.get('pnl', 0):+.2f} USDT · TP1 {ov.get('tp1_rate')} % "
                     f"(nötig ~{ov.get('tp1_breakeven') or '–'} %)")
        live = week.get("live") or {}
        if live.get("with_risk"):
            lines.append(f"Live: {live['with_risk']} Trades · {_r(live.get('exp_r'))} · {live.get('pnl', 0):+.2f} USDT")
        ef = week.get("by_edge_filter") or {}
        for k in ("besteht", "besteht nicht"):
            st = ef.get(k) or {}
            if st.get("with_risk"):
                lines.append(f"Edge-Filter {k}: {st['with_risk']} Trades · {_r(st.get('exp_r'))}")
    if blocks.get("with_risk"):
        lines.append(f"Vom Edge-Filter geblockte Live-Einstiege (Schatten): {blocks['with_risk']} · "
                     f"{_r(blocks.get('exp_r'))} (negativ = Block war richtig)")
    lines += ["", f"*Live-Freigabe-Check:* {ready.get('text') or '–'}"]
    return "\n".join(lines)


async def get_config(db) -> Dict:
    doc = await db.settings.find_one({"_id": SETTINGS_ID}) or {}
    cfg = {k: doc.get(k, v) for k, v in DEFAULTS.items()}
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


async def build_text(db, config: Dict) -> str:
    from services import ai_edge_report
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    week = await ai_edge_report.build(db, config, cutoff)
    return format_report(week, week.get("edge_readiness") or {}, week.get("edge_filter_blocks") or {})


async def send_report(db, config: Dict, trigger: str = "schedule") -> Dict:
    from core import state
    from services import notifications
    text = await build_text(db, config)
    sent = await notifications.telegram_notify(db, state.telegram, "edge_weekly", text)
    now = datetime.now(timezone.utc).isoformat()
    await db.settings.update_one({"_id": SETTINGS_ID},
                                 {"$set": {"last_sent": now, "last_error": None, "last_trigger": trigger}},
                                 upsert=True)
    return {"sent": bool(sent), "report": text, "sent_at": now, "trigger": trigger}


async def weekly_loop(db, config_getter):
    """Hintergrund-Loop: prüft alle 15 min, ob der Wochenbericht fällig ist."""
    await asyncio.sleep(180)
    while True:
        try:
            cfg = await get_config(db)
            if is_due(cfg, datetime.now(BERLIN), cfg.get("last_sent")):
                await send_report(db, config_getter() or {}, trigger="schedule")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Edge-Weekly-Loop: {e}")
            try:
                await db.settings.update_one({"_id": SETTINGS_ID},
                                             {"$set": {"last_error": str(e)[:300]}}, upsert=True)
            except Exception:
                pass
        await asyncio.sleep(CHECK_INTERVAL_S)
