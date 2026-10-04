"""Modell-Wächter: prüft wöchentlich alle konfigurierten Modell-Slugs.

1) Meldet tote Slugs (Modell beim Anbieter entfernt/umbenannt) per Website-
   Benachrichtigung + Telegram – die Fallback-Ketten übernehmen automatisch.
2) Entdeckt NEUE Chat-Modelle in den Live-Katalogen der Provider, schaltet sie
   sofort zur Auswahl frei (ai_providers.DYNAMIC_MODELS, sichtbar im KI-Team &
   AI-Panel) und meldet sie per Website-Glocke + Telegram.

3) (10/2026) Sehr gute neue Modelle (services/ai_model_rating: Stufe "top" +
   erfolgreicher Kurztest) werden OHNE Bestätigung in den Katalog übernommen;
   die Team-KI erklärt in einer Mitteilung (Banner im KI-Team, bleibt bis
   weggeklickt), warum sie gut sind und für welche Rolle (services/ai_model_news).
4) Veraltete Modelle kann der Trader aus dem Katalog entfernen (`removed`) und
   wiederherstellen.

Ergebnis wird in `settings/model_watch` abgelegt und ist über
/api/ai/models/watch abrufbar (manueller Lauf: POST .../run). Der Lauf ist
bewusst leichtgewichtig: 1 Katalog-Request pro Provider, 1x pro Woche.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List

from services import ai_providers

logger = logging.getLogger(__name__)

DOC_ID = "model_watch"
CHECK_EVERY_S = 12 * 3600      # Loop prüft 2x täglich, ob ein Lauf fällig ist
INTERVAL_DAYS = 7              # wöchentlicher Voll-Check (schont die Free-Tiers)
MAX_AUTO_PER_RUN = 6           # höchstens so viele automatische Aufnahmen je Lauf
MAX_ANNOUNCEMENTS = 12


def _key(p: str, m: str) -> str:
    return f"{p}/{m}"


def _apply_runtime(doc: Dict):
    ai_providers.set_dynamic_models(doc.get("approved") or {}, doc.get("approved_meta") or {})
    ai_providers.set_removed_models(doc.get("removed") or {})


class ModelWatch:
    def __init__(self):
        self.running = False

    async def status(self, db) -> Dict:
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        doc.pop("_id", None)
        return {"running": self.running, "interval_days": INTERVAL_DAYS, **doc}

    async def load_discovered(self, db):
        """Beim Boot: nur vom Trader BESTÄTIGTE Modelle wieder freischalten.
        Entdeckte, aber unbestätigte Modelle bleiben gesperrt (Pending)."""
        try:
            doc = await db.settings.find_one({"_id": DOC_ID}) or {}
            _apply_runtime(doc)
            n = sum(len(v) for v in ai_providers.DYNAMIC_MODELS.values())
            if n:
                logger.info(f"Modell-Wächter: {n} bestätigte Modelle wieder aktiv")
            pending = sum(len(v) for v in (doc.get("discovered") or {}).values())
            if pending > n:
                logger.info(f"Modell-Wächter: {pending - n} entdeckte Modelle "
                            "warten auf Bestätigung im KI-Team-Panel")
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
            dismissed = set(prev.get("dismissed") or [])
            discovered = {}
            for p, ms in (result.get("new") or {}).items():
                keep = [m for m in (ms or []) if f"{p}/{m}" not in dismissed]
                if keep:
                    discovered[p] = keep
            brand_new = [f"{p}/{m}" for p, ms in discovered.items() for m in ms
                         if f"{p}/{m}" not in known]
            # Bestätigte Modelle behalten, solange der Anbieter sie noch führt.
            # Bei nicht prüfbaren Providern (kein Key/Katalog down) nichts verwerfen.
            unverified = set(result.get("unverified") or [])
            approved = {}
            for p, ms in (prev.get("approved") or {}).items():
                keep = list(ms or []) if p in unverified else \
                    [m for m in (ms or []) if m in (discovered.get(p) or [])]
                if keep:
                    approved[p] = keep
            approved_meta = dict(prev.get("approved_meta") or {})
            rated = dict(prev.get("rated") or {})
            auto_news = await self._auto_adopt(discovered, approved, approved_meta, rated,
                                               result.get("meta") or {}, dismissed)
            payload = {"checked_at": datetime.now(timezone.utc).isoformat(),
                       "dead": dead,
                       "unverified": sorted(unverified),
                       "providers": result.get("providers") or {},
                       "discovered": discovered,
                       "approved": approved,
                       "dismissed": sorted(dismissed),
                       "last_new": brand_new,
                       "approved_meta": approved_meta,
                       "rated": rated,
                       "manual": bool(manual)}
            await db.settings.update_one({"_id": DOC_ID}, {"$set": payload}, upsert=True)
            # Bestätigte + automatisch aufgenommene (Stufe "top") Modelle werden
            # freigeschaltet – alle anderen warten auf den Bestätigungs-Button.
            _apply_runtime({**prev, **payload})
            if auto_news:
                await self._announce(db, auto_news)
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
                    db, "model_watch_new", "Neue KI-Modelle entdeckt",
                    f"Der Modell-Wächter hat neue Modelle entdeckt: {lst}{more}. "
                    "Sie werden erst nach Bestätigung im KI-Team-Panel auswählbar "
                    "(Button 'Bestätigen').",
                    cooldown_min=60)
                await notifications.telegram_notify(
                    db, state.telegram, "model_watch_new",
                    f"🆕 *NEUE KI-MODELLE ENTDECKT*\n{lst}{more}\n"
                    "Zur Freischaltung im KI-Team-Panel bestätigen.",
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

    async def _auto_adopt(self, discovered: Dict, approved: Dict, approved_meta: Dict,
                          rated: Dict, meta: Dict, dismissed: set) -> List[Dict]:
        """Neu entdeckte Modelle bewerten; Stufe "top" + Kurztest ok -> sofort
        in den Katalog (ohne Bestätigung). Bewertungen werden gemerkt (rated),
        damit jedes Modell nur einmal getestet wird."""
        from services import ai_model_rating
        catalog = ai_providers.catalog()
        fresh = []
        for p, ms in discovered.items():
            for m in ms:
                k = _key(p, m)
                if k in dismissed or m in (approved.get(p) or []):
                    continue
                r = ai_model_rating.rate(p, m, (meta.get(p) or {}).get(m), catalog)
                prev_r = rated.get(k) or {}
                rated[k] = {"tier": r["tier"], "score": r["score"], "reasons": r["reasons"],
                            "paid": r["paid"], "class": r["class"], "price": r["price"],
                            "smoke": prev_r.get("smoke")}
                if r["tier"] == "top":  # Kurztest-Fehler (z.B. 429) -> nächster Lauf erneut
                    fresh.append(r)
        news = []
        for r in sorted(ai_model_rating.newest_only(fresh), key=lambda x: -x["score"]):
            if len(news) >= MAX_AUTO_PER_RUN:
                break
            k = _key(r["provider"], r["model"])
            ok = await self._smoke_test(r["provider"], r["model"])
            rated[k]["smoke"] = "ok" if ok else "fail"
            if not ok:
                continue
            approved.setdefault(r["provider"], []).append(r["model"])
            approved_meta[k] = {"weight": r["weight"], "paid": r["paid"], "auto": True,
                                "added_at": datetime.now(timezone.utc).isoformat()}
            news.append(r)
            logger.info(f"Modell-Wächter: {k} automatisch aufgenommen (Stufe top)")
        return news

    async def _smoke_test(self, provider: str, model: str) -> bool:
        """Ein Mini-Aufruf: antwortet das Modell mit diesem Key überhaupt?"""
        try:
            text, _, _ = await asyncio.wait_for(ai_providers.generate_chain(
                [(provider, model)], "Antworte nur mit OK.", "Du bist ein Test.",
                temperature=0.0, json_mode=False, priority="low"), timeout=60)
            return bool((text or "").strip())
        except Exception as e:  # noqa: BLE001
            logger.info(f"Modell-Wächter: Kurztest {provider}/{model} fehlgeschlagen: {str(e)[:120]}")
            return False

    async def _announce(self, db, news: List[Dict]):
        """Mitteilung (Banner im KI-Team) inkl. Erklärung der Team-KI speichern."""
        from core import state
        from services import ai_model_news, notifications
        ann = await ai_model_news.build_announcement(db, news)
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        anns = ([ann] + list(doc.get("announcements") or []))[:MAX_ANNOUNCEMENTS]
        await db.settings.update_one({"_id": DOC_ID}, {"$set": {"announcements": anns}}, upsert=True)
        lst = ", ".join(_key(r["provider"], r["model"]) for r in news)
        await notifications.website_notify(
            db, "model_watch_auto", "Neue KI-Modelle automatisch aufgenommen",
            f"Sehr gute neue Modelle sind jetzt im Katalog: {lst}. Details & Empfehlung "
            "je Rolle im Reiter KI-Team.", cooldown_min=60)
        await notifications.telegram_notify(
            db, state.telegram, "model_watch_new",
            f"🆕 *NEUE KI-MODELLE IM KATALOG*\n{lst}\nEmpfehlung je Rolle im KI-Team.",
            cooldown_min=60)

    async def dismiss_announcement(self, db, ann_id: str) -> Dict:
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        anns = list(doc.get("announcements") or [])
        hit = False
        for a in anns:
            if a.get("id") == ann_id:
                a["dismissed"] = True
                hit = True
        if not hit:
            return {"status": "error", "detail": "Mitteilung nicht gefunden"}
        await db.settings.update_one({"_id": DOC_ID}, {"$set": {"announcements": anns}})
        return {"status": "ok"}

    async def remove(self, db, provider: str, model: str, in_use: List[str]) -> Dict:
        """Veraltetes Modell aus dem Katalog nehmen. In Benutzung -> abgelehnt."""
        if in_use:
            return {"status": "error",
                    "detail": "Modell wird noch verwendet: " + ", ".join(in_use)
                              + " – bitte dort zuerst ein anderes Modell wählen"}
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        if model in ai_providers.ALLOWED_MODELS.get(provider, []):
            removed = doc.get("removed") or {}
            removed[provider] = sorted(set(removed.get(provider) or []) | {model})
            await db.settings.update_one({"_id": DOC_ID}, {"$set": {"removed": removed}}, upsert=True)
            doc["removed"] = removed
            _apply_runtime(doc)
        elif model in ai_providers.DYNAMIC_MODELS.get(provider, []):
            await self.dismiss(db, provider, model)
        else:
            return {"status": "error", "detail": "Modell nicht im Katalog"}
        logger.info(f"Modell-Wächter: {provider}/{model} vom Trader aus dem Katalog entfernt")
        return {"status": "ok"}

    async def restore(self, db, provider: str, model: str) -> Dict:
        """Entferntes/verworfenes Modell zurückholen (wieder auswählbar)."""
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        removed = doc.get("removed") or {}
        k = _key(provider, model)
        upd: Dict = {}
        if model in (removed.get(provider) or []):
            removed[provider] = [m for m in removed[provider] if m != model]
            if not removed[provider]:
                removed.pop(provider)
            upd["removed"] = removed
        dismissed = list(doc.get("dismissed") or [])
        if k in dismissed:
            upd["dismissed"] = [d for d in dismissed if d != k]
            discovered = doc.get("discovered") or {}
            discovered.setdefault(provider, [])
            if model not in discovered[provider]:
                discovered[provider].append(model)
            approved = doc.get("approved") or {}
            approved.setdefault(provider, [])
            if model not in approved[provider]:
                approved[provider].append(model)
            upd.update({"discovered": discovered, "approved": approved})
        if not upd:
            return {"status": "error", "detail": "Modell ist weder entfernt noch verworfen"}
        await db.settings.update_one({"_id": DOC_ID}, {"$set": upd}, upsert=True)
        _apply_runtime({**doc, **upd})
        return {"status": "ok"}

    async def approve(self, db, provider: str, model: str) -> Dict:
        """Bestätigungs-Button: entdecktes Modell zur Auswahl freischalten."""
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        if model not in ((doc.get("discovered") or {}).get(provider) or []):
            return {"status": "error", "detail": "Modell nicht in den entdeckten Modellen"}
        approved = doc.get("approved") or {}
        cur = list(approved.get(provider) or [])
        if model not in cur:
            cur.append(model)
        approved[provider] = cur
        meta = doc.get("approved_meta") or {}
        r = (doc.get("rated") or {}).get(_key(provider, model)) or {}
        meta[_key(provider, model)] = {"paid": bool(r.get("paid")), "auto": False,
                                       "weight": {"flagship": 3}.get(r.get("class"), 2)}
        await db.settings.update_one(
            {"_id": DOC_ID}, {"$set": {"approved": approved, "approved_meta": meta}}, upsert=True)
        _apply_runtime({**doc, "approved": approved, "approved_meta": meta})
        logger.info(f"Modell-Wächter: {provider}/{model} vom Trader bestätigt")
        return {"status": "ok", "approved": approved}

    async def dismiss(self, db, provider: str, model: str) -> Dict:
        """Entdecktes Modell verwerfen (taucht nicht erneut als 'neu' auf)."""
        doc = await db.settings.find_one({"_id": DOC_ID}) or {}
        discovered = doc.get("discovered") or {}
        if model in (discovered.get(provider) or []):
            discovered[provider] = [m for m in discovered[provider] if m != model]
            if not discovered[provider]:
                discovered.pop(provider)
        approved = doc.get("approved") or {}
        if model in (approved.get(provider) or []):
            approved[provider] = [m for m in approved[provider] if m != model]
            if not approved[provider]:
                approved.pop(provider)
        dismissed = sorted(set(doc.get("dismissed") or []) | {f"{provider}/{model}"})
        await db.settings.update_one(
            {"_id": DOC_ID},
            {"$set": {"discovered": discovered, "approved": approved,
                      "dismissed": dismissed}}, upsert=True)
        _apply_runtime({**doc, "approved": approved})
        logger.info(f"Modell-Wächter: {provider}/{model} verworfen")
        return {"status": "ok"}

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
