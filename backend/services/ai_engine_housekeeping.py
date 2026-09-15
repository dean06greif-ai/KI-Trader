"""Housekeeping des KI Traders (stündliche Bereinigung, Tages-Reset, Tageszusammenfassung).

Ausgelagert aus services/ai_engine.py (Engine-Aufteilung, Juni 2026):
Reines Umheben von Methoden in ein Mixin – KEINE Verhaltensänderung.
Die Klasse AIEngine erbt dieses Mixin; alle Methoden laufen weiterhin auf
derselben Instanz (self.db, self.config, ...) wie zuvor.
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore



logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

SUMMARY_SYSTEM = (
    "Du bist der 'KI Trader'. Fasse den abgelaufenen Trading-Tag prägnant auf Deutsch zusammen. "
    "Antworte AUSSCHLIESSLICH mit reinem Text (kein JSON, kein Markdown-Codeblock). "
    "Struktur (kompakt, max. 12 Zeilen):\n"
    "• Tages-Marktüberblick (2-3 Sätze)\n"
    "• Wichtigste Eckdaten: Anzahl Analysen, ausgelöste Signale, Trade-Entscheidungen (LONG/SHORT/HOLD)\n"
    "• Trader-Direktiven (die vom Nutzer selbst definierten Anweisungen, die die Handelsentscheidungen aktuell steuern)\n"
    "• Aktive Konfiguration (Provider/Modell, Intervall, Min. Konfidenz, Cooldown)\n"
    "Sei nüchtern und ohne Floskeln. Nutze ausschließlich die übergebenen Fakten."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIEngineHousekeepingMixin:
    # ---------------- housekeeping (hourly cleanup + daily reset + summary) ----------------
    async def _persist_housekeeping(self):
        try:
            await self.db.settings.update_one(
                {"_id": "ai_trader_housekeeping"},
                {"$set": {
                    "last_cleanup_hour": self._last_cleanup_hour,
                    "last_reset_date": self._last_reset_date,
                }},
                upsert=True,
            )
        except Exception as e:
            logger.warning(f"AI housekeeping persist failed: {e}")

    async def _cleanup_old_analyses(self) -> int:
        """Löscht alle Nachrichten mit role='analysis' bis auf die neueste.
        User-, Assistant- und Summary-Nachrichten bleiben unangetastet."""
        try:
            latest = await self.db.ai_chat.find_one(
                {"role": "analysis"}, sort=[("ts", -1)],
            )
            if not latest:
                return 0
            query = {"role": "analysis"}
            if latest.get("id"):
                query["id"] = {"$ne": latest["id"]}
            else:
                query["_id"] = {"$ne": latest["_id"]}
            result = await self.db.ai_chat.delete_many(query)
            return result.deleted_count or 0
        except Exception as e:
            logger.error(f"AI hourly cleanup failed: {e}")
            return 0

    async def _collect_daily_facts(self, day_iso: str) -> Dict:
        """Sammelt die Fakten des abgelaufenen Tages aus ai_chat (vor dem Löschen)
        + ai_decisions. `day_iso` = YYYY-MM-DD (Berlin) des Tages, der zusammengefasst wird."""
        # Alles was aktuell im Chat liegt = Tages-Nachrichten (Hourly-Cleanup hat alte
        # analysis-Einträge bereits weg-geräumt, außerdem darf hier eine ältere Summary
        # liegen – die kommt in den Archivierungs-Snapshot).
        chat_docs = await self.db.ai_chat.find().sort("ts", 1).to_list(length=None)

        # ai_decisions: filtere nach Berlin-Datum. ts ist ISO in UTC.
        all_dec = await self.db.ai_decisions.find({"ts": {"$exists": True}}).sort("ts", 1).to_list(length=None)
        day_dec = []
        for d in all_dec:
            try:
                dt = datetime.fromisoformat(str(d.get("ts", "")).replace("Z", "+00:00"))
                if dt.astimezone(BERLIN_TZ).strftime("%Y-%m-%d") == day_iso:
                    day_dec.append(d)
            except Exception:
                continue

        analyses = [c for c in chat_docs if c.get("role") == "analysis"]
        directives = [c for c in chat_docs if c.get("role") == "user"]
        assistants = [c for c in chat_docs if c.get("role") == "assistant"]
        summaries_prev = [c for c in chat_docs if c.get("role") == "summary"]

        signals = [d for d in day_dec if d.get("signaled")]
        actions = {"LONG": 0, "SHORT": 0, "HOLD": 0}
        for d in day_dec:
            a = str(d.get("action", "HOLD")).upper()
            if a in actions:
                actions[a] += 1

        overviews = [str(a.get("text") or "").strip() for a in analyses if a.get("text")]

        return {
            "day": day_iso,
            "chat_docs": chat_docs,
            "day_decisions": day_dec,
            "counts": {
                "analyses": len(analyses),
                "decisions": len(day_dec),
                "signals": len(signals),
                "long": actions["LONG"],
                "short": actions["SHORT"],
                "hold": actions["HOLD"],
                "directives": len(directives),
                "assistant_msgs": len(assistants),
                "prev_summaries": len(summaries_prev),
            },
            "signals": [f"{s.get('symbol')} {s.get('action')} ({s.get('confidence')}%)" for s in signals],
            "directives": [str(d.get("text") or "").strip() for d in directives if d.get("text")],
            "overviews": overviews,
        }

    def _statistical_summary(self, facts: Dict) -> str:
        """Fallback-Zusammenfassung, wenn die LLM nicht erreichbar ist."""
        c = facts["counts"]
        cfg = self.config
        parts = [
            f"Tages-Zusammenfassung ({facts['day']}) – statistischer Fallback (LLM nicht erreichbar).",
            f"• Analysen: {c['analyses']} · Entscheidungen: {c['decisions']} "
            f"(LONG {c['long']} / SHORT {c['short']} / HOLD {c['hold']}) · "
            f"Ausgelöste Signale: {c['signals']}",
        ]
        if facts["signals"]:
            parts.append("• Signale: " + ", ".join(facts["signals"][:12]))
        if facts["overviews"]:
            latest_ov = facts["overviews"][-1][:220]
            parts.append(f"• Letzter Marktüberblick: {latest_ov}")
        if facts["directives"]:
            dirs = " | ".join(d[:120] for d in facts["directives"][-6:])
            parts.append(f"• Trader-Direktiven (aktuell aktiv): {dirs}")
        else:
            parts.append("• Trader-Direktiven: (keine vom Nutzer im Chat gesetzt)")
        parts.append(
            f"• Aktive Konfiguration: Provider {cfg.get('provider')} / Modell {cfg.get('model')} · "
            f"Intervall {cfg.get('interval_min')} min · Min. Konfidenz {cfg.get('min_confidence')}% · "
            f"Cooldown {cfg.get('cooldown_min')} min · News {'an' if cfg.get('news_enabled') else 'aus'}"
        )
        return "\n".join(parts)

    async def _llm_daily_summary(self, facts: Dict) -> Optional[str]:
        """Generiert die Zusammenfassung via aktivem LLM-Provider. Gibt None bei Fehler."""
        if not self.key:
            return None
        cfg = self.config
        c = facts["counts"]
        directives_block = "\n".join(f"- {d}" for d in facts["directives"][-15:]) or "(keine)"
        signals_block = "\n".join(f"- {s}" for s in facts["signals"][:20]) or "(keine)"
        overviews_block = "\n".join(f"- {o[:220]}" for o in facts["overviews"][-6:]) or "(keine)"
        prompt = (
            f"Zusammenfassung für Tag: {facts['day']} (Europe/Berlin)\n\n"
            f"KENNZAHLEN:\n"
            f"- Analysen: {c['analyses']}\n"
            f"- Entscheidungen: {c['decisions']} (LONG {c['long']} / SHORT {c['short']} / HOLD {c['hold']})\n"
            f"- Ausgelöste Signale: {c['signals']}\n\n"
            f"SIGNALE:\n{signals_block}\n\n"
            f"MARKTÜBERBLICKE (chronologisch, ältester zuerst):\n{overviews_block}\n\n"
            f"TRADER-DIREKTIVEN (vom Nutzer im Chat gesetzt, definieren wonach gerade getradet wird):\n{directives_block}\n\n"
            f"AKTIVE KONFIGURATION:\n"
            f"- Provider/Modell: {cfg.get('provider')} / {cfg.get('model')}\n"
            f"- Analyse-Intervall: {cfg.get('interval_min')} min\n"
            f"- Min. Konfidenz: {cfg.get('min_confidence')}%\n"
            f"- Trade-Cooldown: {cfg.get('cooldown_min')} min\n"
            f"- News-Feed: {'an' if cfg.get('news_enabled') else 'aus'}\n\n"
            f"Erstelle nun die kompakte deutsche Tages-Zusammenfassung wie im System-Prompt beschrieben."
        )
        provider = cfg.get("provider", "gemini")
        try:
            text, _p, _m = await self.generate_for_role(
                "summarizer", prompt, SUMMARY_SYSTEM, temperature=0.4, json_mode=False)
            return text or None
        except Exception as e:
            logger.warning(f"Daily summary {provider} failed: {e}")
            return None

    async def _daily_reset(self, prev_day_iso: str) -> Dict:
        """Archiviert Tages-Chat + Entscheidungen, generiert eine markierte
        Tages-Zusammenfassung und pinnt sie oben im Chat.

        Reihenfolge (WICHTIG: kein Datenverlust bei LLM- oder DB-Fehlern):
        1) Fakten sammeln
        2) Summary-Text generieren (LLM + Fallback)
        3) Archivieren
        4) Cutoff-Delete (nur Vortags-Nachrichten `ts < Mitternacht Berlin`) –
           nach Mitternacht neu eingetroffene Nachrichten bleiben erhalten
        5) Summary einfügen (ts = Mitternacht Berlin des neuen Tages, damit
           sie chronologisch VOR allen Neuer-Tag-Nachrichten liegt)
        6) Ältere gepinnte Summaries entpinnen (nur die neueste ist pinned)
        """
        # 1) Fakten sammeln – zwingend VOR jeglicher Löschaktion.
        facts = await self._collect_daily_facts(prev_day_iso)

        # 2) Zusammenfassung generieren (LLM + Fallback). Der Fallback liefert
        #    IMMER einen Text, damit wir nie mit leerer Summary weiterlaufen.
        text = await self._llm_daily_summary(facts)
        used_fallback = False
        if not text:
            text = self._statistical_summary(facts)
            used_fallback = True

        # Cutoff = Mitternacht Berlin des NEUEN Tages (= Ende von prev_day_iso).
        # Alle Nachrichten mit ts < cutoff gehören zum Vortag und werden gelöscht.
        try:
            cutoff_dt_berlin = datetime.strptime(prev_day_iso, "%Y-%m-%d") \
                .replace(tzinfo=BERLIN_TZ) + timedelta(days=1)
        except Exception:
            cutoff_dt_berlin = datetime.now(BERLIN_TZ)
        cutoff_utc_iso = cutoff_dt_berlin.astimezone(timezone.utc).isoformat()

        # 3) Archivieren – KI vergisst nichts.
        archive_batch = str(uuid.uuid4())
        archive_ts = _now_iso()
        archive_errors = False
        try:
            if facts["chat_docs"]:
                docs = []
                for c in facts["chat_docs"]:
                    d = dict(c)
                    d.pop("_id", None)
                    d["archive_batch"] = archive_batch
                    d["archive_day"] = prev_day_iso
                    d["archived_at"] = archive_ts
                    d["source"] = "ai_chat"
                    docs.append(d)
                await self.db.ai_chat_archive.insert_many(docs)
            if facts["day_decisions"]:
                docs = []
                for c in facts["day_decisions"]:
                    d = dict(c)
                    d.pop("_id", None)
                    d["archive_batch"] = archive_batch
                    d["archive_day"] = prev_day_iso
                    d["archived_at"] = archive_ts
                    d["source"] = "ai_decisions"
                    docs.append(d)
                await self.db.ai_chat_archive.insert_many(docs)
        except Exception as e:
            archive_errors = True
            logger.error(f"AI daily archive failed: {e}")
            # Best-Effort: Archiv-Fehler blockieren den Chat-Reset nicht,
            # sonst würde die Engine ewig mit vollem Chat weiterlaufen.

        # 4) Cutoff-Delete: nur echte Vortags-Nachrichten löschen. Verhindert,
        #    dass Nachrichten aus dem neuen Tag (Race Condition zwischen 00:00
        #    und dem Ende der Summary-Generierung) versehentlich mit-gelöscht
        #    werden.
        delete_ok = False
        try:
            await self.db.ai_chat.delete_many({"ts": {"$lt": cutoff_utc_iso}})
            delete_ok = True
        except Exception as e:
            logger.error(f"AI daily chat clear failed: {e}")
            # Wir versuchen trotzdem, die Summary einzufügen (siehe 5) – der
            # Nutzer soll wenigstens den Tages-Bericht sehen.

        # 5) Summary einfügen. ts = cutoff (Mitternacht Berlin des neuen Tages),
        #    dadurch sortiert die Summary chronologisch VOR allen neu
        #    eingetroffenen Nachrichten und bleibt auch beim `sort("ts", -1)`
        #    Fenster relevant, wenn wir sie in chat_history() explizit pinnen.
        cfg = self.config
        summary_doc = {
            "id": str(uuid.uuid4()),
            "role": "summary",
            "pinned": True,
            "text": text,
            "day": prev_day_iso,
            "counts": facts["counts"],
            "directives": facts["directives"][-15:],
            "active_config": {
                "provider": cfg.get("provider"),
                "model": cfg.get("model"),
                "interval_min": cfg.get("interval_min"),
                "min_confidence": cfg.get("min_confidence"),
                "cooldown_min": cfg.get("cooldown_min"),
                "news_enabled": cfg.get("news_enabled"),
            },
            "fallback": used_fallback,
            "archive_batch": archive_batch,
            "archive_errors": archive_errors,
            "ts": cutoff_utc_iso,
        }
        summary_inserted = False
        try:
            await self.db.ai_chat.insert_one(dict(summary_doc))
            summary_inserted = True
        except Exception as e:
            logger.error(f"AI daily summary insert failed: {e}")

        # 6) Nur die NEUESTE Summary bleibt gepinnt – alle älteren entpinnen.
        #    Verhindert Doppel-Pins nach mehreren Reset-Läufen und stellt sicher,
        #    dass das Frontend immer genau eine gepinnte Summary sieht.
        if summary_inserted:
            try:
                await self.db.ai_chat.update_many(
                    {"role": "summary", "pinned": True, "id": {"$ne": summary_doc["id"]}},
                    {"$set": {"pinned": False}},
                )
            except Exception as e:
                logger.warning(f"AI daily summary un-pin previous failed: {e}")

        # 7) Automatischer Lernlauf: die KI analysiert die eröffneten/geschlossenen
        #    Trades des Tages und leitet daraus Lektionen für die Zukunft ab.
        learning_status = None
        if summary_inserted and self.learning \
                and self.config.get("learning_enabled", True) and self.key:
            try:
                lres = await self.learning.run_learning(trigger="daily_summary")
                learning_status = lres.get("status")
                logger.info(f"AI daily learning ({prev_day_iso}): {learning_status}")
            except Exception as e:
                logger.warning(f"AI daily learning failed: {e}")

        logger.info(
            f"AI daily reset done for {prev_day_iso}: archived {len(facts['chat_docs'])} chat + "
            f"{len(facts['day_decisions'])} decisions, summary via "
            f"{'FALLBACK' if used_fallback else 'LLM'}, delete_ok={delete_ok}, "
            f"summary_inserted={summary_inserted}"
        )
        return {
            "day": prev_day_iso,
            "archived_chat": len(facts["chat_docs"]),
            "archived_decisions": len(facts["day_decisions"]),
            "fallback": used_fallback,
            "summary_id": summary_doc["id"],
            "summary_inserted": summary_inserted,
            "delete_ok": delete_ok,
            "archive_errors": archive_errors,
            "learning": learning_status,
        }

    async def _run_housekeeping(self):
        """Wird vom run_loop jede Iteration angetriggert. Führt bei Bedarf
        (1) stündliches Analyse-Cleanup und (2) 00:00-Berlin Tages-Reset aus.

        Der Tages-Reset-Marker (`_last_reset_date`) wird AUSSCHLIESSLICH nach
        einem nachweislich erfolgreichen Reset fortgeschrieben – schlägt der
        Reset fehl (z. B. DB-Fehler beim Insert der Summary), wird er im
        nächsten Loop-Durchlauf automatisch erneut versucht. Nach 5 erfolglosen
        Versuchen wird der Marker zwangs-fortgeschrieben und ein Error geloggt,
        damit die Engine nicht dauerhaft blockiert bleibt."""
        async with self._housekeeping_lock:
            now_berlin = datetime.now(BERLIN_TZ)
            hour_key = now_berlin.strftime("%Y%m%d%H")
            date_key = now_berlin.strftime("%Y-%m-%d")

            # (A) Tages-Reset zuerst: neuer Kalendertag Berlin?
            if self._last_reset_date and date_key != self._last_reset_date:
                prev_day = self._last_reset_date

                # Retry-Zähler pro anstehendem Vortag verwalten.
                if self._reset_retry_day != prev_day:
                    self._reset_retry_day = prev_day
                    self._reset_retry_count = 0

                # Notbremse: nach 5 Fehlversuchen Marker fortschreiben, damit
                # die Engine nicht dauerhaft am selben Tag festhängt.
                if self._reset_retry_count >= 5:
                    logger.error(
                        f"Daily reset for {prev_day} skipped after "
                        f"{self._reset_retry_count} failed attempts – marker advanced."
                    )
                    self._last_reset_date = date_key
                    self._last_cleanup_hour = hour_key
                    self._reset_retry_day = None
                    self._reset_retry_count = 0
                    await self._persist_housekeeping()
                    return

                success = False
                try:
                    result = await self._daily_reset(prev_day)
                    # Erfolg = Summary konnte tatsächlich in ai_chat geschrieben
                    # werden. Nur dann darf der Marker fortgeschritten werden,
                    # sonst würde die Summary für diesen Tag ausfallen.
                    success = bool(result.get("summary_inserted"))
                except Exception as e:
                    logger.error(
                        f"Daily reset error "
                        f"(attempt {self._reset_retry_count + 1}/5) for {prev_day}: {e}"
                    )

                if not success:
                    self._reset_retry_count += 1
                    logger.warning(
                        f"Daily reset for {prev_day} not successful, "
                        f"will retry ({self._reset_retry_count}/5)."
                    )
                    # Marker NICHT fortschreiben -> nächster Loop-Durchlauf retried.
                    return

                # Erst nach echtem Erfolg: Lernlauf + Marker fortschreiben.
                try:
                    if self.learning and self.config.get("learning_enabled", True) and self.key:
                        await self.learning.run_learning(trigger="daily")
                except Exception as e:
                    logger.error(f"Daily learning error: {e}")
                self._last_reset_date = date_key
                # Nach Reset ist auch die aktuelle Stunde als 'gecleant' zu markieren
                # (der Chat ist ohnehin leer bis auf die Summary).
                self._last_cleanup_hour = hour_key
                self._reset_retry_day = None
                self._reset_retry_count = 0
                await self._persist_housekeeping()
                return

            # (B) Stündliches Cleanup – exakt zur vollen Stunde einmal pro Stunde.
            if self._last_cleanup_hour and hour_key != self._last_cleanup_hour:
                try:
                    removed = await self._cleanup_old_analyses()
                    if removed:
                        logger.info(f"AI hourly cleanup: {removed} alte Analyse-Nachricht(en) entfernt.")
                except Exception as e:
                    logger.error(f"Hourly cleanup error: {e}")
                self._last_cleanup_hour = hour_key
                await self._persist_housekeeping()

    async def force_daily_summary(self) -> Dict:
        """Manueller Trigger (Endpoint): erzwingt Reset + Summary für den 'aktuellen
        Berlin-Tag' (bzw. dem Marker `_last_reset_date`).

        Marker wird NUR nach nachweislich erfolgreichem Reset fortgeschrieben,
        damit ein Fehler nicht die reguläre Mitternachts-Logik überspringt."""
        prev_day = self._last_reset_date or datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")
        result = await self._daily_reset(prev_day)
        if result.get("summary_inserted"):
            self._last_reset_date = datetime.now(BERLIN_TZ).strftime("%Y-%m-%d")
            self._last_cleanup_hour = datetime.now(BERLIN_TZ).strftime("%Y%m%d%H")
            self._reset_retry_day = None
            self._reset_retry_count = 0
            await self._persist_housekeeping()
        return result
