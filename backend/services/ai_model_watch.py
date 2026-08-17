"""Modell-Wächter: prüft wöchentlich alle konfigurierten Modell-Slugs.

1) Meldet tote Slugs (Modell beim Anbieter entfernt/umbenannt) per Website-
   Benachrichtigung + Telegram – die Fallback-Ketten übernehmen automatisch.
2) Entdeckt NEUE Chat-Modelle in den Live-Katalogen der Provider, schaltet sie
   sofort zur Auswahl frei (ai_providers.DYNAMIC_MODELS, sichtbar im KI-Team &
   AI-Panel) und meldet sie per Website-Glocke + Telegram.

Ergebnis wird in `settings/model_watch` abgelegt und ist über
/api/ai/models/watch abrufbar (manueller Lauf: POST .../run). Der Lauf ist
bewusst leichtgewichtig: 1 Katalog-Request pro Provider, 1x pro Woche.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict

from services import ai_providers

logger = logging.getLogger(__name__)

DOC_ID = "model_watch"
CHECK_EVERY_S = 12 * 3600      # Loop prüft 2x täglich, ob ein Lauf fällig ist
INTERVAL_DAYS = 7              # wöchentlicher Voll-Check (schont die Free-Tiers)


class ModelWatch:
    def __init__(self):
        self.running = False

    async def status(self, db) -> Dict:
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        doc.pop("_id", None)
        return {"running": self.running, "interval_days": INTERVAL_DAYS, **doc}

    async def load_discovered(self, db):
        """Beim Boot: früher entdeckte Modelle direkt wieder freischalten."""
        try:
            doc = await db.settings.find_one({"_id": DOC_ID}) or {}
            disc = doc.get("discovered") or {}
            if disc:
                ai_providers.set_dynamic_models(disc)
                n = sum(len(v) for v in ai_providers.DYNAMIC_MODELS.values())
                logger.info(f"Modell-Wächter: {n} früher entdeckte Modelle wieder aktiv")
        except Exception as e:
            logger.warning(f"Modell-Wächter load_discovered: {e}")

    async def run_check(self, db, manual: bool = False) -> Dict:
        if self.running:
            return {"status": "busy", "detail": "Modell-Check läuft bereits"}
        self.running = True
        try:
            prev = await db.settings.find_one({"_id": DOC_ID}) or {}
            first_baseline = "discovered" not in prev  # 1. Lauf: nur Basis speichern, nicht spammen
            known = {f"{p}/{m}" for p, ms in (prev.get("discovered") or {}).items()
                     for m in (ms or [])}
            result = await ai_providers.verify_catalog()
            dead = result.get("dead") or []
            discovered = {p: ms for p, ms in (result.get("new") or {}).items() if ms}
            brand_new = [f"{p}/{m}" for p, ms in discovered.items() for m in ms
                         if f"{p}/{m}" not in known]
            payload = {"checked_at": datetime.now(timezone.utc).isoformat(),
                       "dead": dead,
                       "unverified": result.get("unverified") or [],
                       "providers": result.get("providers") or {},
                       "discovered": discovered,
                       "last_new": brand_new,
                       "manual": bool(manual)}
            await db.settings.update_one({"_id": DOC_ID}, {"$set": payload}, upsert=True)
            ai_providers.set_dynamic_models(discovered)
            from core import state
            from services import notifications
            if dead:
                lst = ", ".join(dead[:8])
                await notifications.website_notify(
                    db, "model_watch", "Modell-Wächter: tote Modell-Slugs erkannt",
                    f"Diese konfigurierten KI-Modelle existieren beim Anbieter nicht mehr: {lst}. "
                    "Die Fallback-Ketten übernehmen automatisch – bitte im KI-Team ein anderes "
                    "Modell wählen.", cooldown_min=60)
                await notifications.telegram_notify(
                    db, state.telegram, "model_watch",
                    f"🛰️ *MODELL-WÄCHTER*\nTote Modell-Slugs erkannt: {lst}\n"
                    "Fallbacks übernehmen – bitte Modelle im KI-Team aktualisieren.",
                    cooldown_min=60)
                logger.warning(f"Modell-Wächter: tote Slugs -> {dead}")
            if brand_new and not first_baseline:
                lst = ", ".join(brand_new[:10])
                more = f" (+{len(brand_new) - 10} weitere)" if len(brand_new) > 10 else ""
                await notifications.website_notify(
                    db, "model_watch_new", "Neue KI-Modelle verfügbar",
                    f"Der Modell-Wächter hat neue Modelle entdeckt: {lst}{more}. "
                    "Sie sind ab sofort im KI-Team und im AI-Panel auswählbar.",
                    cooldown_min=60)
                await notifications.telegram_notify(
                    db, state.telegram, "model_watch_new",
                    f"🆕 *NEUE KI-MODELLE VERFÜGBAR*\n{lst}{more}\n"
                    "Ab sofort im KI-Team & AI-Panel auswählbar.",
                    cooldown_min=60)
                logger.info(f"Modell-Wächter: neue Modelle entdeckt -> {brand_new}")
            if not dead and not brand_new:
                logger.info("Modell-Wächter: alle Modelle verfügbar, nichts Neues")
            return {"status": "ok", **payload}
        except Exception as e:
            logger.error(f"Modell-Wächter fehlgeschlagen: {e}")
            return {"status": "error", "detail": str(e)[:200]}
        finally:
            self.running = False

    async def run_loop(self):
        """Hintergrund-Loop: wöchentlicher Check (2 Min nach Boot erstmals geprüft)."""
        from core import state
        await asyncio.sleep(120)
        if state.db is not None:
            await self.load_discovered(state.db)
        while True:
            try:
                db = state.db
                if db is not None:
                    doc = await db.settings.find_one({"_id": DOC_ID}) or {}
                    due = True
                    last = doc.get("checked_at")
                    if last:
                        try:
                            age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
                            due = age.days >= INTERVAL_DAYS
                        except ValueError:
                            due = True
                    if due:
                        await self.run_check(db)
            except Exception as e:
                logger.warning(f"Modell-Wächter-Loop: {e}")
            await asyncio.sleep(CHECK_EVERY_S)


model_watch = ModelWatch()
