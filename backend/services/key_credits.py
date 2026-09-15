"""Guthaben-Wächter für bezahlte OpenRouter-Keys (Paid-Modelle).

Fragt periodisch das Rest-Guthaben (total_credits - total_usage, Endpoint
/api/v1/credits) aller konfigurierten OpenRouter-Keys ab (Haupt + Copilot,
inkl. Backups). Schwellen (vom Trader festgelegt):
  < WARN_USD  (2.00 $)  -> Warnung   (gelbes Banner + Telegram)
  < CRIT_USD  (0.50 $)  -> Kritisch  (rotes Banner + Telegram)
Free-Tier-Keys ohne jemals geladenes Guthaben werden NICHT gewarnt (dort gibt
es kein Guthaben, nur Tageslimits – dafür existiert die Key-Ampel).
Telegram: höchstens 1 Meldung je Key & Stufe pro 24h (settings-Dedupe)."""
import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WARN_USD = 2.0
CRIT_USD = 0.5
CHECK_EVERY_MIN = 30
NOTIFY_COOLDOWN_H = 24
STATE_ID = "key_credits_state"

_snapshot: Dict = {"checked_at": None, "keys": [], "worst_level": "ok",
                   "warn_below_usd": WARN_USD, "critical_below_usd": CRIT_USD}


def level_for(remaining: Optional[float], has_credits: bool) -> str:
    """Warn-Stufe (rein & testbar). Keys ohne je geladenes Guthaben: 'free'."""
    if remaining is None:
        return "unknown"
    if not has_credits:
        return "free"
    if remaining < CRIT_USD:
        return "critical"
    if remaining < WARN_USD:
        return "warning"
    return "ok"


def _watched_keys() -> List[Tuple[str, str]]:
    """(Label, Key) aller OpenRouter-Keys: Haupt + Copilot inkl. Backups."""
    out, seen = [], set()
    for env, label in (("OPENROUTER_API_KEY", "OpenRouter"),
                       ("COPILOT_OPENROUTER_API_KEY", "Copilot")):
        for suffix in [""] + [f"_BACKUP{i}" if i else "_BACKUP" for i in range(0, 11)]:
            k = (os.environ.get(f"{env}{suffix}") or "").strip()
            if k and k not in seen:
                seen.add(k)
                n = suffix.replace("_BACKUP", "Backup ") or "Haupt"
                out.append((f"{label} {n}".strip(), k))
    return out


async def _fetch_credits(client, key: str) -> Dict:
    r = await client.get("https://openrouter.ai/api/v1/credits",
                         headers={"Authorization": f"Bearer {key}"})
    d = (r.json() or {}).get("data") or {}
    total = float(d.get("total_credits") or 0)
    usage = float(d.get("total_usage") or 0)
    return {"total_credits": total, "usage": round(usage, 4),
            "remaining": round(total - usage, 4)}


async def check_once(db=None, telegram=None) -> Dict:
    import httpx
    rows = []
    async with httpx.AsyncClient(timeout=15) as client:
        for label, key in _watched_keys():
            entry = {"label": label, "key_masked": f"{key[:14]}…{key[-4:]}"}
            try:
                c = await _fetch_credits(client, key)
                lvl = level_for(c["remaining"], c["total_credits"] > 0)
                entry.update({**c, "level": lvl})
            except Exception as e:  # noqa: BLE001
                entry.update({"level": "unknown", "error": str(e)[:100]})
            rows.append(entry)
    order = {"critical": 3, "warning": 2, "ok": 1, "free": 0, "unknown": 0}
    worst = max(rows, key=lambda r: order.get(r["level"], 0), default=None)
    _snapshot.update({
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "keys": rows,
        "worst_level": (worst or {}).get("level", "ok")
        if worst and order.get(worst["level"], 0) >= 2 else "ok",
    })
    if db is not None:
        await _maybe_notify(db, telegram, rows)
    return dict(_snapshot)


async def _maybe_notify(db, telegram, rows: List[Dict]) -> None:
    alerts = [r for r in rows if r.get("level") in ("warning", "critical")]
    if not alerts:
        return
    try:
        state = await db.settings.find_one({"_id": STATE_ID}) or {}
        sent = state.get("sent") or {}
        now = time.time()
        fresh = []
        for r in alerts:
            k = f"{r['key_masked']}:{r['level']}"
            if now - float(sent.get(k) or 0) > NOTIFY_COOLDOWN_H * 3600:
                sent[k] = now
                fresh.append(r)
        if not fresh:
            return
        await db.settings.update_one({"_id": STATE_ID},
                                     {"$set": {"sent": sent}}, upsert=True)
        lines = [("🔴" if r["level"] == "critical" else "🟡")
                 + f" {r['label']} ({r['key_masked']}): nur noch "
                 + f"{r.get('remaining', 0):.2f} $ Guthaben"
                 for r in fresh]
        from services import notifications
        await notifications.telegram_notify(
            db, telegram, "key_credits",
            "⚠️ *KEY-GUTHABEN NIEDRIG* (Paid-Modelle)\n" + "\n".join(lines)
            + f"\n\nSchwellen: Warnung < {WARN_USD:.2f} $, kritisch < {CRIT_USD:.2f} $. "
              "Guthaben aufladen: openrouter.ai/settings/credits")
    except Exception as e:
        logger.warning(f"key credits notify failed: {e}")


def snapshot() -> Dict:
    return dict(_snapshot)


async def run_loop():
    await asyncio.sleep(90)     # Boot abwarten
    while True:
        try:
            from core import state
            await check_once(state.db, state.telegram)
        except Exception as e:
            logger.error(f"key credits loop error: {e}")
        await asyncio.sleep(CHECK_EVERY_MIN * 60)
