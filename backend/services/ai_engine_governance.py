"""Self-Tuning-Governance des KI Traders (Vorschläge, Leitplanken, Autonomie-Review).

Ausgelagert aus services/ai_engine.py (Engine-Aufteilung, Juni 2026):
Reines Umheben von Methoden in ein Mixin – KEINE Verhaltensänderung.
Die Klasse AIEngine erbt dieses Mixin; alle Methoden laufen weiterhin auf
derselben Instanz (self.db, self.config, ...) wie zuvor.
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

from services.ai_knowledge import validate_changes
from services.ai_master_prompt import master_prompt
from services.ai_strategy_lab import strategy_lab
from services import ai_validation
from services.ai_validation import validation_gate


logger = logging.getLogger(__name__)

BERLIN_TZ = ZoneInfo("Europe/Berlin")

OPINION_SYSTEM = (
    "Du bist der 'KI Trader'. Der Trader hat eine Änderung an deinen Vorgaben vorgenommen. "
    "Sie gilt sofort – du kannst sie nicht blockieren. Sage aber ehrlich und datenbasiert deine "
    "Meinung: stimmt sie mit deiner Erfahrung überein, oder hast du einen Einwand? "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown:\n"
    '{"stance": "zustimmung|einwand|neutral", "comment": "2-4 Sätze auf Deutsch", '
    '"risk": "kurz, falls Risiko – sonst leer"}'
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AIEngineGovernanceMixin:
    # ---------------- self-tuning (KI ändert eigene Trade-Einstellungen) ----------------
    async def _current_cfg_values(self, scope: str, symbol: Optional[str], keys) -> Dict:
        if scope == "engine":
            return {k: self.config.get(k) for k in keys}
        if scope == "candidate":
            # Makro-Parameter einer eigenen KI-Strategie (Kandidat)
            cand = await strategy_lab.get(symbol) or {}
            macro = cand.get("macro_params") or {}
            from core.defaults import DEFAULT_STRATEGY_COIN_CFG
            return {k: macro.get(k, DEFAULT_STRATEGY_COIN_CFG.get(k)) for k in keys}
        from core.defaults import DEFAULT_STRATEGY_COIN_CFG
        doc = await self.db.strategy_coin_configs.find_one({"_id": f"ai_trader_{symbol}"})
        saved = doc.get("config", {}) if doc else {}
        merged = {**DEFAULT_STRATEGY_COIN_CFG, **saved}
        return {k: merged.get(k) for k in keys}

    async def _apply_changes(self, scope: str, symbol: Optional[str], changes: Dict):
        if scope == "engine":
            await self.update_config(dict(changes))
            return
        if scope == "candidate":
            await strategy_lab.update_macro_params(symbol, dict(changes))
            return
        key = f"ai_trader_{symbol}"
        changes = dict(changes)
        # Hebel-Wirksamkeit (RCA 26.08.): ein fester Hebel wirkt nur, wenn die
        # Auto-Hebel-Formel aus ist – sonst überschreibt effective_leverage()
        # den bestätigten Wert (Beweis: BTC 7.68 bestätigt, Trades liefen mit
        # 25x = auto_lev_max). Explizit mitgeschicktes Flag wird respektiert.
        if "leverage" in changes and "auto_leverage_enabled" not in changes:
            changes["auto_leverage_enabled"] = False
        doc = await self.db.strategy_coin_configs.find_one({"_id": key})
        saved = doc.get("config", {}) if doc else {}
        saved.update(changes)
        await self.db.strategy_coin_configs.replace_one(
            {"_id": key}, {"_id": key, "config": saved}, upsert=True)
        try:
            from core.state import autotrader  # lazy: kein Zyklus beim Import
            autotrader.config.setdefault("strategy_coin_configs", {})[key] = saved
        except Exception:
            pass

    async def _insert_proposal(self, prop: Dict):
        """Vorschlag speichern + Proposal-Aufräumer: ältere OFFENE Duplikate
        (gleicher Scope, gleiches Symbol, gleiche Änderungs-Keys) werden als
        'superseded' geschlossen – verhindert, dass sich hunderte identische
        Vorschläge stapeln (Befund 26.08.: ~285 offene Hebel-Proposals).
        Blockierte/fehlgeschlagene neue Vorschläge verdrängen nichts."""
        if prop.get("status") in ("pending", "needs_data", "needs_confirmation",
                                  "auto_applied", "applied"):
            try:
                keys = sorted((prop.get("changes") or {}).keys())
                if keys:
                    cand = await self.db.ai_proposals.find(
                        {"status": {"$in": ["pending", "needs_data", "needs_confirmation"]},
                         "scope": prop.get("scope"), "symbol": prop.get("symbol"),
                         "id": {"$ne": prop.get("id")}},
                        {"id": 1, "changes": 1}).to_list(500)
                    old_ids = [c["id"] for c in cand
                               if sorted((c.get("changes") or {}).keys()) == keys]
                    if old_ids:
                        await self.db.ai_proposals.update_many(
                            {"id": {"$in": old_ids}},
                            {"$set": {"status": "superseded", "decided_at": _now_iso(),
                                      "decision_note": "Auto: durch neueren Vorschlag ersetzt"}})
            except Exception as e:
                logger.warning(f"Proposal-Aufräumer übersprungen: {e}")
        await self.db.ai_proposals.insert_one(dict(prop))

    async def _macro_gate(self, prop: Dict, macro_keys: List[str], current: Dict,
                          stats: Dict, scope: str, symbol: Optional[str]):
        """Struktur-Parameter (SL, CRV, Hebel ...) brauchen mehrere Bestätigungen
        und dürfen nur in kleinen Schritten wandern.

        Die Bestätigungen werden aus früheren Vorschlägen derselben Richtung
        gezählt – ein einzelner (Verlust-)Trade verschiebt damit nichts."""
        window_days = int(validation_gate.settings.get("macro_confirm_window_days", 14))
        since = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sample = await self._macro_sample(stats, scope, symbol)
        worst = None
        clamped_any = False
        changes = dict(prop["changes"])
        for key in macro_keys:
            cur, proposed = current.get(key), changes[key]
            direction = "up" if (cur is None or float(proposed) > float(cur)) else "down"
            try:
                confirmations = 1 + await self.db.ai_proposals.count_documents({
                    "scope": scope,
                    "symbol": prop["symbol"],
                    f"changes.{key}": {"$exists": True},
                    "macro_direction": direction,
                    "ts": {"$gte": since},
                })
            except Exception:
                confirmations = 1
            gate = validation_gate.macro(sample, confirmations)
            gate["key"] = key
            gate["direction"] = direction
            if worst is None or (not gate["validated"] and worst["validated"]):
                worst = gate
            value, was_clamped = validation_gate.clamp(key, cur, proposed)
            changes[key] = value
            clamped_any = clamped_any or was_clamped
        prop["changes"] = changes
        prop["macro_direction"] = worst.get("direction") if worst else None
        return (worst or {"validated": True, "reason": "keine Makro-Parameter"}), clamped_any

    async def _macro_sample(self, stats: Dict, scope: str, symbol: Optional[str]) -> int:
        """Stichprobe für Makro-Änderungen – pro Kandidat aus dessen eigenen Trades."""
        if scope == "candidate" and symbol:
            try:
                return await self.db.auto_trades.count_documents(
                    {"ai_candidate_id": symbol, "status": "closed"})
            except Exception:
                return 0
        return ai_validation.sample_size(stats, scope, symbol)

    def _tuning_guard(self, changes: Dict) -> str:
        """Self-Tuning-Guard: Engine-Änderungen der KI nur innerhalb der vom
        Trader definierten Leitplanken (Spanne einstellbar, KI darf sie nie
        ändern). Rückgabe: leerer String = ok, sonst Begründung."""
        lo = int(self.config.get("tune_conf_min", 55) or 0)
        hi = int(self.config.get("tune_conf_max", 75) or 100)
        cd_max = int(self.config.get("tune_cooldown_max", 45) or 0)
        if "min_confidence" in changes:
            try:
                v = int(changes["min_confidence"])
            except (TypeError, ValueError):
                return "min_confidence kein gültiger Wert"
            if not (lo <= v <= hi):
                return (f"min_confidence {v}% liegt außerhalb der Autonomie-Spanne "
                        f"{lo}–{hi}% – nur der Trader darf das bestätigen")
        if "cooldown_min" in changes:
            try:
                c = int(changes["cooldown_min"])
            except (TypeError, ValueError):
                return "cooldown_min kein gültiger Wert"
            if cd_max and c > cd_max:
                return (f"cooldown_min {c} min über dem Autonomie-Limit "
                        f"{cd_max} min – nur der Trader darf das bestätigen")
        if "max_same_direction" in changes:
            try:
                v = int(changes["max_same_direction"])
            except (TypeError, ValueError):
                return "max_same_direction kein gültiger Wert"
            g_lo = int(self.config.get("tune_guard_min", 1) or 1)
            g_hi = int(self.config.get("tune_guard_max", 6) or 6)
            if v == 0 or not (g_lo <= v <= g_hi):
                return (f"Richtungs-Guard auf {v} "
                        f"({'aus' if v == 0 else f'außerhalb der Autonomie-Spanne {g_lo}–{g_hi}'}) "
                        "– nur der Trader darf das bestätigen")
        if "maker_suspend_hours" in changes:
            try:
                h = float(changes["maker_suspend_hours"])
            except (TypeError, ValueError):
                return "maker_suspend_hours kein gültiger Wert"
            if h <= 0:
                return ("Maker-Order-Modus wieder aktivieren "
                        "– nur der Trader darf das bestätigen")
            if h > 72:
                return (f"Maker-Aussetzung {h:g}h (> 72h) "
                        "– nur der Trader darf das bestätigen")
        if "correlation_guard" in changes:
            # Trader-Schalter: JEDE Änderung (an UND aus) braucht Bestätigung.
            # Bugfix: die KI durfte den Guard automatisch wieder EINSCHALTEN und
            # drehte damit den manuell ausgeschalteten Zustand des Traders
            # ständig zurück – jetzt bleibt der Trader-Zustand bestehen, bis er
            # selbst bestätigt.
            state = "einschalten" if changes["correlation_guard"] else "abschalten"
            return (f"Korrelations-Guard {state} – Trader-Schalter, "
                    "nur der Trader darf das bestätigen")
        return ""

    async def _normalize_auto_tuned(self):
        """Boot-Heilung: Wenn der AKTUELLE Engine-Wert außerhalb der Leitplanken
        liegt UND nachweislich von der KI selbst gesetzt wurde (auto_applied-
        Proposal mit exakt diesem Wert), wird er auf die Leitplanken-Grenze
        zurückgeholt. Manuell vom Trader gesetzte Werte werden NIE angefasst;
        jedes Proposal wird höchstens einmal normalisiert (guard_normalized)."""
        if self.db is None:
            return
        lo = int(self.config.get("tune_conf_min", 55) or 0)
        hi = int(self.config.get("tune_conf_max", 75) or 100)
        cd_max = int(self.config.get("tune_cooldown_max", 45) or 0)
        fixes: Dict = {}
        notes: List[str] = []
        for key, bound, ok in (
                ("min_confidence", hi, lambda v: lo <= v <= hi),
                ("cooldown_min", cd_max, lambda v: not cd_max or v <= cd_max)):
            try:
                cur = int(self.config.get(key) or 0)
            except (TypeError, ValueError):
                continue
            if ok(cur):
                continue
            prop = await self.db.ai_proposals.find_one({
                "scope": "engine", "status": "auto_applied",
                f"changes.{key}": cur, "guard_normalized": {"$ne": True},
            }, sort=[("ts", -1)])
            if not prop:
                continue  # nicht von der KI gesetzt (oder schon normalisiert)
            fixes[key] = bound
            notes.append(f"{key} {cur} → {bound}")
            await self.db.ai_proposals.update_one(
                {"id": prop.get("id")}, {"$set": {"guard_normalized": True}})
        if not fixes:
            return
        self.config.update(fixes)
        await self.db.settings.update_one(
            {"_id": "ai_trader_config"}, {"$set": fixes}, upsert=True)
        msg = ("Self-Tuning-Guard: Die KI hatte ihre Engine-Werte außerhalb der "
               "Autonomie-Leitplanken gesetzt – zurückgeholt: " + ", ".join(notes) +
               ". Die Spanne ist in den KI-Einstellungen anpassbar.")
        logger.warning(msg)
        try:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "governance",
                "text": msg, "ts": _now_iso()})
        except Exception:
            pass

    async def _handle_config_changes(self, raw_list: List, source: str = "analysis") -> List[Dict]:
        """Validiert KI-Änderungswünsche gegen die Whitelist und wendet sie an
        (autonomy=auto) bzw. legt sie als bestätigungspflichtige Vorschläge ab
        (autonomy=suggest). max_capital & mode sind hart gesperrt."""
        autonomy = self.config.get("autonomy", "suggest")
        if autonomy not in ("suggest", "auto") or not raw_list:
            return []
        upper_syms = {s.upper(): s for s in self.symbols}
        # Datenbasis für die Validierung (nur für KI-initiierte Änderungen nötig)
        stats: Dict = {}
        if source != "user" and self.learning:
            try:
                stats = await self.learning.gather_stats()
            except Exception as e:
                logger.warning(f"Validierungs-Statistik nicht verfügbar: {e}")
        results = []
        for item in raw_list[:6]:
            if not isinstance(item, dict):
                continue
            symbol_raw = str(item.get("symbol", "")).upper().strip()
            cand_ref = str(item.get("strategy_candidate_id") or "").strip() or (
                symbol_raw.lower() if symbol_raw.lower().startswith("cand_") else "")
            if cand_ref:
                scope, symbol = "candidate", cand_ref
                if not await strategy_lab.get(cand_ref):
                    logger.info(f"AI config change: Kandidat {cand_ref} unbekannt – übersprungen")
                    continue
            else:
                scope = "engine" if symbol_raw in ("ENGINE", "GLOBAL", "") else "coin"
                symbol = upper_syms.get(symbol_raw)
                if scope == "coin" and not symbol:
                    continue
            # Kandidaten nutzen dieselbe Whitelist wie Coin-Configs
            valid, rejected = validate_changes(item.get("changes") or {},
                                               scope="coin" if scope == "candidate" else scope)
            if rejected:
                logger.info(f"AI config change abgelehnt ({symbol_raw}): {rejected}")
            if not valid:
                continue
            current = await self._current_cfg_values(scope, symbol, valid.keys())
            valid = {k: v for k, v in valid.items() if current.get(k) != v}
            if not valid:
                continue
            prop = {
                "id": str(uuid.uuid4()),
                "ts": _now_iso(),
                "scope": scope,
                "symbol": symbol if scope in ("coin", "candidate") else "ENGINE",
                "changes": valid,
                "current": {k: current.get(k) for k in valid},
                "reason": str(item.get("reason", ""))[:300],
                "source": source,
                "status": "pending",
            }
            # 1. MasterPrompt (oberstes Gebot) – harte Sperre
            master_ok, master_why = master_prompt.check_changes(valid)
            if not master_ok and source != "user":
                prop["status"] = "blocked_master"
                prop["block_reason"] = master_why
                await self._insert_proposal(prop)
                results.append(prop)
                logger.info(f"AI config change durch MasterPrompt blockiert: {master_why}")
                continue
            # 2. Datenbasis-Validierung – ohne ausreichende Stichprobe nur parken
            if source != "user":
                macro_keys = [k for k in valid if ai_validation.is_macro_key(k)]
                normal_keys = [k for k in valid if k not in macro_keys]
                gate = validation_gate.change(stats, scope, symbol) if normal_keys else \
                    {"validated": True, "reason": "nur Struktur-Parameter", "sample": 0}
                prop["validation"] = gate
                if normal_keys and not gate.get("validated"):
                    prop["status"] = "needs_data"
                    await self._insert_proposal(prop)
                    results.append(prop)
                    logger.info(f"AI config change geparkt (needs_data): {gate.get('reason')}")
                    continue
                if macro_keys:
                    macro_gate, clamped = await self._macro_gate(
                        prop, macro_keys, current, stats, scope, symbol)
                    prop["macro_validation"] = macro_gate
                    prop["clamped"] = clamped
                    if not macro_gate.get("validated"):
                        prop["status"] = "needs_confirmation"
                        await self._insert_proposal(prop)
                        results.append(prop)
                        logger.info(f"AI Makro-Änderung geparkt: {macro_gate.get('reason')}")
                        continue
                    valid = prop["changes"]
            # 3. Self-Tuning-Guard: Engine-Werte außerhalb der Leitplanken
            # werden NIE automatisch angewendet – nur Vorschlag an den Trader.
            if scope == "engine" and source != "user":
                guard_why = self._tuning_guard(valid)
                if guard_why:
                    prop["status"] = "needs_confirmation"
                    prop["guard_reason"] = guard_why
                    await self._insert_proposal(prop)
                    results.append(prop)
                    logger.info(f"AI Engine-Änderung durch Autonomie-Leitplanke "
                                f"geparkt: {guard_why}")
                    continue
            if autonomy == "auto" or source == "user":
                try:
                    await self._apply_changes(scope, symbol, valid)
                    prop["status"] = "auto_applied"
                    prop["decided_at"] = _now_iso()
                except Exception as e:
                    prop["status"] = "error"
                    prop["error"] = str(e)[:200]
            await self._insert_proposal(prop)
            results.append(prop)
        if results:
            applied = [p for p in results if p["status"] == "auto_applied"]
            pending = [p for p in results if p["status"] == "pending"]
            parked = [p for p in results if p["status"] == "needs_data"]
            # Autonomie "auto": geparkte Wünsche (needs_data/needs_confirmation)
            # still sammeln statt den Trader mit Hinweisen zu fluten – die KI
            # schlägt sie automatisch erneut vor, sobald die Daten reichen.
            if autonomy == "auto" and source != "user" and not applied and not pending \
                    and not any(p["status"] in ("blocked_master", "error") for p in results):
                return results
            unconfirmed = [p for p in results if p["status"] == "needs_confirmation"]
            blocked = [p for p in results if p["status"] == "blocked_master"]
            txt = []
            if applied:
                txt.append("Ich habe meine Trade-Einstellungen angepasst (Autonomie: automatisch).")
            if pending:
                txt.append("Ich schlage Änderungen an meinen Trade-Einstellungen vor – bitte bestätigen oder ablehnen.")
            if parked:
                txt.append(f"{len(parked)} Änderung(en) warten auf mehr Daten "
                           f"({parked[0].get('validation', {}).get('reason', '')}).")
            if unconfirmed:
                txt.append(f"{len(unconfirmed)} Struktur-Änderung(en) (SL/CRV/Hebel) warten auf "
                           f"weitere Bestätigungen: "
                           f"{unconfirmed[0].get('macro_validation', {}).get('reason', '')}")
            if blocked:
                txt.append(f"{len(blocked)} Änderung(en) verstoßen gegen den MasterPrompt "
                           f"und wurden verworfen ({blocked[0].get('block_reason', '')}).")
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "config",
                "text": " ".join(txt),
                "items": [{"proposal_id": p["id"], "symbol": p["symbol"],
                           "changes": p["changes"], "current": p["current"],
                           "reason": p["reason"], "status": p["status"],
                           "validation": p.get("validation"),
                           "macro_validation": p.get("macro_validation"),
                           "clamped": p.get("clamped"),
                           "block_reason": p.get("block_reason")} for p in results],
                "source": source, "ts": _now_iso(),
            })
        return results

    # ---------------- geparkte Vorschläge (Autonomie "auto") ----------------
    async def _close_proposal(self, pid: str, status: str, extra: Optional[Dict] = None):
        """Status eines Vorschlags final setzen und im KI-Feed spiegeln."""
        patch = {"status": status, "decided_at": _now_iso(), **(extra or {})}
        await self.db.ai_proposals.update_one({"id": pid}, {"$set": patch})
        try:
            await self.db.ai_chat.update_many(
                {"role": "config", "items.proposal_id": pid},
                {"$set": {"items.$.status": status}})
        except Exception:
            pass

    async def review_parked_proposals(self, limit: int = 30) -> Dict:
        """Autonomie "auto": geparkte Änderungswünsche (needs_data /
        needs_confirmation) erneut gegen die AKTUELLE Datenlage prüfen und
        automatisch anwenden, sobald die Validierung sie freigibt.

        Damit muss der Trader im autonomen Modus nichts mehr bestätigen – die
        bestehende Validierung (Stichprobe, Bestätigungen, Schrittweite,
        MasterPrompt) bleibt aber vollständig wirksam. Im Modus "suggest"
        passiert hier nichts: dort entscheidet der Trader per Karte."""
        if self.db is None or self.config.get("autonomy") != "auto":
            return {"reviewed": 0, "applied": 0}
        try:
            rows = await self.db.ai_proposals.find({
                "status": {"$in": ["needs_data", "needs_confirmation"]},
                "source": {"$ne": "user"},
            }).sort("ts", -1).limit(limit).to_list(limit)
        except Exception as e:
            logger.warning(f"Geparkte Vorschläge konnten nicht geladen werden: {e}")
            return {"reviewed": 0, "applied": 0}
        if not rows:
            return {"reviewed": 0, "applied": 0}
        stats: Dict = {}
        if self.learning:
            try:
                stats = await self.learning.gather_stats()
            except Exception as e:
                logger.warning(f"Validierungs-Statistik nicht verfügbar: {e}")
        applied: List[Dict] = []
        for prop in rows:
            prop.pop("_id", None)
            pid = prop.get("id")
            scope = prop.get("scope", "coin")
            sym_field = prop.get("symbol")
            symbol = None if scope == "engine" else sym_field
            changes = dict(prop.get("changes") or {})
            if not pid or not changes:
                continue
            ok, why = master_prompt.check_changes(changes)
            if not ok:
                await self._close_proposal(pid, "blocked_master", {"block_reason": why})
                continue
            # Self-Tuning-Guard: geparkte Engine-Vorschläge außerhalb der
            # Leitplanken bleiben Vorschlag – der Trader muss sie bestätigen.
            if scope == "engine":
                guard_why = self._tuning_guard(changes)
                if guard_why:
                    await self.db.ai_proposals.update_one({"id": pid}, {"$set": {
                        "guard_reason": guard_why, "reviewed_at": _now_iso()}})
                    continue
            macro_keys = [k for k in changes if ai_validation.is_macro_key(k)]
            normal_keys = [k for k in changes if k not in macro_keys]
            gate = validation_gate.change(stats, scope, sym_field) if normal_keys else \
                {"validated": True, "reason": "nur Struktur-Parameter", "sample": 0}
            if normal_keys and not gate.get("validated"):
                await self.db.ai_proposals.update_one(
                    {"id": pid}, {"$set": {"validation": gate, "reviewed_at": _now_iso()}})
                continue
            current = await self._current_cfg_values(scope, symbol, changes.keys())
            if macro_keys:
                probe = {"changes": changes, "symbol": sym_field}
                macro_gate, clamped = await self._macro_gate(
                    probe, macro_keys, current, stats, scope, sym_field)
                if not macro_gate.get("validated"):
                    await self.db.ai_proposals.update_one({"id": pid}, {"$set": {
                        "macro_validation": macro_gate, "clamped": clamped,
                        "reviewed_at": _now_iso()}})
                    continue
                changes = probe["changes"]
            changes = {k: v for k, v in changes.items() if current.get(k) != v}
            if not changes:
                await self._close_proposal(pid, "obsolete")
                continue
            try:
                await self._apply_changes(scope, symbol, changes)
            except Exception as e:
                await self._close_proposal(pid, "error", {"error": str(e)[:200]})
                continue
            await self._close_proposal(pid, "auto_applied", {
                "changes": changes, "validation": gate,
                "current": {k: current.get(k) for k in changes}})
            applied.append({"proposal_id": pid, "symbol": prop.get("symbol"),
                            "changes": changes,
                            "current": {k: current.get(k) for k in changes},
                            "reason": prop.get("reason", ""), "status": "auto_applied"})
        if applied:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "config",
                "text": f"{len(applied)} zurückgestellte Änderung(en) sind jetzt durch die "
                        "Datenlage bestätigt und wurden automatisch übernommen "
                        "(Autonomie: automatisch).",
                "items": applied, "source": "auto_review", "ts": _now_iso(),
            })
            logger.info(f"Autonomie-Review: {len(applied)} geparkte Änderung(en) angewendet")
        return {"reviewed": len(rows), "applied": len(applied)}

    async def actionable_proposals(self, limit: int = 30) -> List[Dict]:
        """Vorschläge, die WIRKLICH eine Entscheidung des Traders brauchen.

        Im autonomen Modus ist das immer eine leere Liste – dort verwaltet die
        KI ihre Wünsche selbst (siehe `review_parked_proposals`). Damit kann das
        Frontend die Karten ohne Race-Condition ausblenden."""
        if self.config.get("autonomy") == "auto":
            return []
        rows = await self.db.ai_proposals.find({
            "status": {"$in": ["pending", "needs_confirmation", "needs_data"]},
        }).sort("ts", -1).limit(max(1, min(100, limit))).to_list(100)
        for r in rows:
            r.pop("_id", None)
        return rows

    async def comment_on_user_change(self, topic: str, detail: str) -> Dict:
        """Der Trader hat etwas geändert – die KI sagt ehrlich ihre Meinung dazu.
        Blockiert nichts, landet als Eintrag (role='opinion') im KI-Feed."""
        if self.db is None:
            return {"status": "skipped", "detail": "keine DB"}
        if not self.key:
            return {"status": "skipped", "detail": "kein API-Key"}
        try:
            stats_txt = await self.learning.performance_text() if self.learning else ""
            lessons_txt = await self.learning.lessons_text() if self.learning else ""
            prompt = (
                f"{master_prompt.prompt_block()}\n\n"
                f"=== ÄNDERUNG DES TRADERS ({topic}) ===\n{detail}\n\n"
                f"=== DEINE PERFORMANCE ===\n{stats_txt}\n\n"
                f"=== DEINE LEKTIONEN ===\n{lessons_txt}\n\n"
                "Bewerte diese Änderung ehrlich. Sie gilt ohnehin – aber wenn deine Daten "
                "dagegen sprechen, sage klar warum."
            )
            text, provider, model = await self.generate_for_role(
                "chat", prompt, OPINION_SYSTEM, temperature=0.3)
            data = self._parse_json(text)
            entry = {
                "id": str(uuid.uuid4()), "role": "opinion",
                "topic": topic,
                "stance": str(data.get("stance", "neutral"))[:20],
                "text": str(data.get("comment", ""))[:900],
                "risk": str(data.get("risk", ""))[:300],
                "model": model, "ts": _now_iso(),
            }
            await self.db.ai_chat.insert_one(dict(entry))
            logger.info(f"KI-Meinung zu '{topic}': {entry['stance']} ({provider}/{model})")
            return {"status": "ok", **entry}
        except Exception as e:
            logger.warning(f"KI-Meinung zu '{topic}' fehlgeschlagen: {e}")
            return {"status": "error", "detail": str(e)[:200]}

    async def list_proposals(self, status: Optional[str] = None, limit: int = 40) -> List[Dict]:
        q = {"status": status} if status else {}
        rows = await self.db.ai_proposals.find(q).sort("ts", -1).limit(limit).to_list(limit)
        for r in rows:
            r.pop("_id", None)
        return rows

    async def decide_proposal(self, pid: str, approve: bool) -> Optional[Dict]:
        prop = await self.db.ai_proposals.find_one({"id": pid})
        # Der Trader darf auch geparkte Vorschläge (fehlende Daten/Bestätigungen)
        # freigeben – seine Entscheidung braucht keine Validierung.
        if not prop or prop.get("status") not in ("pending", "needs_data",
                                                 "needs_confirmation"):
            return None
        if approve:
            symbol = None if prop.get("scope") == "engine" else prop.get("symbol")
            await self._apply_changes(prop.get("scope", "coin"), symbol, prop.get("changes") or {})
        new_status = "applied" if approve else "rejected"
        await self.db.ai_proposals.update_one(
            {"id": pid}, {"$set": {"status": new_status, "decided_at": _now_iso()}})
        try:
            await self.db.ai_chat.update_many(
                {"role": "config", "items.proposal_id": pid},
                {"$set": {"items.$.status": new_status}})
        except Exception:
            pass
        prop.pop("_id", None)
        prop["status"] = new_status
        return prop
