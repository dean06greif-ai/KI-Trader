"""Policy-Labor (Audit 3.2): Champion vs. Kandidat.

Der Champion (aktuelle Policy) handelt unverändert weiter (live/paper wie bisher).
Eine Kandidaten-Policy (Prompt-Zusatz und/oder Sizing-/Engine-Overrides) läuft
parallel als SHADOW-Entscheidung auf DENSELBEN Gruppen-Prompts/Marktsnapshots:
eigener LLM-Lauf, keine echten Signale, Ergebnisse als simulierte Paper-Trades
MIT Kosten (Spread/Slippage via services/paper_execution.py + Taker-Fees) in der
Collection `policy_trials`. Token-Deckel: der Kandidat läuft nur bei jedem k-ten
Analyse-Zyklus (`shadow_every_k`, Default 3).

Promotion-Regel (Wechsel Champion -> Kandidat) folgt in Audit 3.3 – hier nur Messung.
Reine Kernfunktionen auf Modulebene (unit-testbar ohne Netzwerk).
"""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from services import paper_execution
from services import policy_fingerprint
from services import policy_promotion
from services import setup_asset_class
from services.ai_strategy_lab import ghost_outcome

logger = logging.getLogger(__name__)

TRIALS_COLL = "policy_trials"
CAND_COLL = "policy_candidates"
SETTINGS_DOC = "policy_lab"

DEFAULT_SETTINGS = {
    "enabled": True,
    "shadow_every_k": 3,        # Kandidat läuft nur bei jedem k-ten Analyse-Zyklus (Token-Deckel)
    "trial_timeout_min": 240,   # Trial ohne SL/TP-Treffer verfällt (wie Ghost-Trades)
    "max_open_trials": 40,      # Deckel offener Trials (Schutz vor Spam)
    "fee_pct": 0.06,            # Taker-Fee je Seite in % (Netto-Abrechnung)
}

# Engine-Settings, die ein Kandidat übersteuern darf (Sizing + Einstiegs-Schwelle)
ALLOWED_OVERRIDE_KEYS = tuple(policy_fingerprint.SIZING_KEYS) + ("min_confidence",)
MAX_SUFFIX_LEN = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- reine Kernfunktionen ----------------

def validate_candidate(spec: Dict) -> tuple:
    """(ok, grund, bereinigt): Kandidat braucht Namen und mindestens EINE Änderung
    (system_suffix und/oder config_overrides aus ALLOWED_OVERRIDE_KEYS)."""
    if not isinstance(spec, dict):
        return False, "Spezifikation fehlt", None
    name = str(spec.get("name") or "").strip()[:80]
    if not name:
        return False, "Name fehlt", None
    suffix = str(spec.get("system_suffix") or "").strip()[:MAX_SUFFIX_LEN]
    raw_ov = spec.get("config_overrides") or {}
    overrides = {k: raw_ov[k] for k in ALLOWED_OVERRIDE_KEYS
                 if isinstance(raw_ov, dict) and k in raw_ov}
    if not suffix and not overrides:
        return False, ("Keine Änderung: system_suffix oder config_overrides "
                       f"({', '.join(ALLOWED_OVERRIDE_KEYS)}) angeben"), None
    return True, "", {
        "name": name, "system_suffix": suffix, "config_overrides": overrides,
        "note": str(spec.get("note") or "")[:300],
    }


def candidate_fingerprint(cand: Dict, champion_prompt_hash: str, lessons_h: str,
                          playbook_version: str, model: Optional[str],
                          gate_version, base_cfg: Dict) -> Dict:
    """Policy-Fingerprint des Kandidaten: Champion-Prompt + Suffix, Sizing mit Overrides."""
    p_hash = policy_fingerprint.short_hash(
        [str(champion_prompt_hash or ""), str(cand.get("system_suffix") or "")])
    sizing_h = policy_fingerprint.sizing_hash(
        {**(base_cfg or {}), **(cand.get("config_overrides") or {})})
    return policy_fingerprint.build(
        prompt_hash=p_hash, lessons_h=lessons_h, playbook_version=playbook_version,
        model=model, gate_version=gate_version, sizing_h=sizing_h)


def net_pnl_pct(side: str, entry_eff: float, exit_eff: float, fee_pct: float) -> float:
    """Netto-Ergebnis in % auf effektive Fill-Preise, abzüglich Fees beider Seiten."""
    if not entry_eff:
        return 0.0
    move = (exit_eff - entry_eff) if str(side).upper() == "LONG" else (entry_eff - exit_eff)
    return round(move / entry_eff * 100 - 2 * float(fee_pct or 0), 4)


def trial_stats(rows: List[Dict]) -> Dict:
    """Aggregation der Trials (Netto): expired zählt nicht in die Winrate."""
    closed = [r for r in (rows or []) if r.get("status") == "closed"]
    wins = sum(1 for r in closed if r.get("result") == "win")
    losses = sum(1 for r in closed if r.get("result") == "loss")
    decided = wins + losses
    pnl = round(sum(float(r.get("net_pnl_pct") or 0) for r in closed), 4)
    return {
        "trials": len(rows or []),
        "open": sum(1 for r in (rows or []) if r.get("status") == "open"),
        "closed": len(closed), "wins": wins, "losses": losses,
        "expired": sum(1 for r in (rows or []) if r.get("status") == "expired"),
        "win_rate": round(wins / decided * 100, 1) if decided else 0.0,
        "net_pnl_pct": pnl,
        "avg_net_pnl_pct": round(pnl / decided, 4) if decided else 0.0,
    }


def should_shadow(cycle_n: int, every_k: int) -> bool:
    return int(every_k or 1) > 0 and int(cycle_n) % max(1, int(every_k or 1)) == 0


# ---------------- Labor (Singleton) ----------------

class PolicyLab:
    def __init__(self):
        self.engine = None
        self.settings: Dict = dict(DEFAULT_SETTINGS)
        self.promotion_cfg: Dict = dict(policy_promotion.DEFAULTS)
        self.candidate: Optional[Dict] = None   # aktiver Kandidat (Cache)
        self._cycle_n = 0
        self._promo_check_ts = 0.0
        self.last_shadow: Optional[str] = None
        self.last_error: Optional[str] = None

    def setup(self, engine):
        self.engine = engine

    @property
    def db(self):
        return self.engine.db if self.engine else None

    async def load_state(self):
        if self.db is None:
            return
        doc = await self.db.settings.find_one({"_id": SETTINGS_DOC}) or {}
        for k in DEFAULT_SETTINGS:
            if k in doc:
                self.settings[k] = doc[k]
        for k in policy_promotion.DEFAULTS:
            if k in (doc.get("promotion") or {}):
                self.promotion_cfg[k] = doc["promotion"][k]
        self.candidate = await self.db[CAND_COLL].find_one(
            {"status": {"$in": ["active", "promotion_ready", "promoted"]}}, {"_id": 0})

    async def update_settings(self, updates: Dict) -> Dict:
        for key, lo, hi in (("shadow_every_k", 1, 20), ("trial_timeout_min", 15, 2880),
                            ("max_open_trials", 5, 200)):
            if key in updates:
                try:
                    self.settings[key] = max(lo, min(hi, int(updates[key])))
                except (TypeError, ValueError):
                    pass
        if "fee_pct" in updates:
            try:
                self.settings["fee_pct"] = max(0.0, min(0.5, float(updates["fee_pct"])))
            except (TypeError, ValueError):
                pass
        if "enabled" in updates:
            self.settings["enabled"] = bool(updates["enabled"])
        promo = updates.get("promotion")
        if isinstance(promo, dict):
            for key, lo, hi in (("min_trials", 5, 500), ("champ_min_trades", 3, 200),
                                ("bootstrap_n", 100, 5000),
                                ("rollback_window_trades", 5, 200),
                                ("rollback_min_trades", 3, 100)):
                if key in promo:
                    try:
                        self.promotion_cfg[key] = max(lo, min(hi, int(promo[key])))
                    except (TypeError, ValueError):
                        pass
            for key, lo, hi in (("bootstrap_alpha", 0.01, 0.25), ("dd_tolerance", 0.0, 10.0)):
                if key in promo:
                    try:
                        self.promotion_cfg[key] = max(lo, min(hi, float(promo[key])))
                    except (TypeError, ValueError):
                        pass
        await self.db.settings.update_one(
            {"_id": SETTINGS_DOC},
            {"$set": {**dict(self.settings), "promotion": dict(self.promotion_cfg)}},
            upsert=True)
        return {**dict(self.settings), "promotion": dict(self.promotion_cfg)}

    async def create_candidate(self, spec: Dict, source: str = "trader") -> Dict:
        ok, why, cleaned = validate_candidate(spec)
        if not ok:
            return {"status": "rejected", "reason": why}
        if self.candidate:
            return {"status": "rejected",
                    "reason": f"Es läuft bereits Kandidat „{self.candidate.get('name')}“ – "
                              "erst verwerfen (oder 3.3-Promotion abwarten)"}
        doc = {"id": f"pol_{uuid.uuid4().hex[:10]}", **cleaned, "source": source,
               "status": "active", "created_at": _now_iso()}
        await self.db[CAND_COLL].insert_one(dict(doc))
        self.candidate = doc
        logger.info(f"Policy-Kandidat angelegt: {doc['name']} ({doc['id']})")
        return {"status": "ok", "candidate": doc}

    async def discard_candidate(self, reason: str = "") -> Dict:
        if not self.candidate:
            return {"status": "rejected", "reason": "Kein aktiver Kandidat"}
        if self.candidate.get("status") == "promoted":
            return {"status": "rejected",
                    "reason": "Kandidat ist promoted – bitte Rollback statt Verwerfen"}
        cid = self.candidate["id"]
        await self.db[CAND_COLL].update_one({"id": cid}, {"$set": {
            "status": "discarded", "discarded_at": _now_iso(),
            "discard_reason": str(reason or "")[:300]}})
        logger.info(f"Policy-Kandidat verworfen: {cid} ({reason})")
        self.candidate = None
        return {"status": "ok", "id": cid}

    # ---------------- Analyse-Zyklus-Hooks ----------------

    def begin_cycle(self) -> bool:
        """Einmal je run_analysis: True, wenn DIESER Zyklus den Kandidaten fährt."""
        if (not self.settings.get("enabled", True) or not self.candidate
                or self.candidate.get("status") not in ("active", "promotion_ready")):
            return False
        self._cycle_n += 1
        return should_shadow(self._cycle_n, self.settings.get("shadow_every_k", 3))

    async def shadow_group(self, engine, g_label: str, g_prompt: str, sys_prompt: str,
                           snaps: Dict, g_syms, champion_prompt_hash: str,
                           lessons_h: str = "", playbook_version: str = "",
                           gate_version=None) -> int:
        """Kandidaten-Lauf auf derselben Gruppe: eigener LLM-Call, Trials speichern."""
        cand = self.candidate
        if not cand:
            return 0
        open_n = await self.db[TRIALS_COLL].count_documents({"status": "open"})
        if open_n >= int(self.settings.get("max_open_trials", 40)):
            logger.info("Policy-Lab: max_open_trials erreicht – Shadow-Lauf übersprungen")
            return 0
        suffix = str(cand.get("system_suffix") or "")
        c_sys = (sys_prompt + ("\n\n=== KANDIDATEN-ÄNDERUNG (Policy-Labor) ===\n" + suffix
                               if suffix else ""))
        raw, model_used = await engine._generate_json(g_prompt, c_sys)
        data = engine._parse_json(raw)
        overrides = cand.get("config_overrides") or {}
        min_conf = int(overrides.get("min_confidence")
                       or engine.config.get("min_confidence", 65) or 0)
        fp = candidate_fingerprint(cand, champion_prompt_hash, lessons_h,
                                   playbook_version, model_used, gate_version,
                                   engine.config)
        n = 0
        for d in data.get("decisions", []):
            sym = d.get("symbol")
            action = str(d.get("action", "HOLD")).upper()
            if sym not in snaps or sym not in g_syms or action not in ("LONG", "SHORT"):
                continue
            conf = max(0, min(100, int(d.get("confidence", 0) or 0)))
            if conf < min_conf:
                continue
            entry = float(snaps[sym].get("price") or 0)
            if entry <= 0:
                continue
            is_swing = str(d.get("horizon") or "").lower() == "swing"
            lv = setup_asset_class.clamp_levels(
                setup_asset_class.asset_class_of(sym),
                float(d.get("sl_pct", 0.6) or 0.6),
                float(d.get("tp1_pct", 0.9) or 0.9),
                float(d.get("tpf_pct", 1.8) or 1.8), is_swing)
            sign = 1 if action == "LONG" else -1
            sl = entry * (1 - sign * lv["sl_pct"] / 100)
            tp = entry * (1 + sign * lv["tp1_pct"] / 100)
            try:
                entry_eff, exec_info = await paper_execution.entry_fill(sym, action, entry)
            except Exception as pe:  # Kosten-Ermittlung darf den Trial nicht verhindern
                logger.debug(f"Policy-Lab entry_fill {sym}: {pe}")
                entry_eff, exec_info = entry, None
            await self.db[TRIALS_COLL].insert_one({
                "id": f"trial_{uuid.uuid4().hex[:10]}", "candidate_id": cand["id"],
                "symbol": str(sym).upper(), "side": action,
                "entry": entry, "entry_eff": float(entry_eff or entry),
                "sl": sl, "tp": tp, "confidence": conf,
                "setup": d.get("setup"), "horizon": "swing" if is_swing else "scalp",
                "reason": str(d.get("reasoning") or "")[:300],
                "group": g_label, "model": model_used,
                "policy_version": fp, "paper_exec": exec_info,
                "status": "open", "opened_at": _now_iso(),
            })
            n += 1
        if n:
            self.last_shadow = _now_iso()
            logger.info(f"Policy-Lab: {n} Shadow-Trial(s) für Kandidat "
                        f"{cand['id']} ({g_label})")
        return n

    # ---------------- Auswertung (im Ökosystem-Tick) ----------------

    async def tick(self):
        if self.db is None or not self.settings.get("enabled", True):
            return
        try:
            await self._evaluate_trials()
            if time.time() - self._promo_check_ts >= 1800:  # max. alle 30 min prüfen
                self._promo_check_ts = time.time()
                await self._check_promotion()
                await self._check_rollback()
        except Exception as e:  # noqa: BLE001 – Tick darf die Engine nie stoppen
            self.last_error = str(e)[:200]
            logger.error(f"Policy-Lab Auswertung fehlgeschlagen: {e}")

    async def _evaluate_trials(self):
        rows = await self.db[TRIALS_COLL].find({"status": "open"}).limit(200).to_list(200)
        if not rows:
            return
        fee = float(self.settings.get("fee_pct", 0.06) or 0)
        timeout_min = int(self.settings.get("trial_timeout_min", 240))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=timeout_min)
        for t in rows:
            try:
                opened = datetime.fromisoformat(str(t.get("opened_at")))
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                opened = None
            if opened and opened < cutoff:
                await self.db[TRIALS_COLL].update_one({"id": t["id"]}, {"$set": {
                    "status": "expired", "result": "expired", "closed_at": _now_iso()}})
                continue
            try:
                price = self.engine.scanner.current_price(t["symbol"])
            except Exception:
                price = None
            if not price:
                continue
            res = ghost_outcome(t["side"], float(price), float(t["sl"]), float(t["tp"]))
            if not res:
                continue
            exit_price = float(t["tp"]) if res == "win" else float(t["sl"])
            try:
                exit_eff, exit_exec = await paper_execution.exit_fill(
                    t["symbol"], t["side"], exit_price)
            except Exception as pe:  # noqa: BLE001
                logger.debug(f"Policy-Lab exit_fill {t['symbol']}: {pe}")
                exit_eff, exit_exec = exit_price, None
            pnl = net_pnl_pct(t["side"], float(t.get("entry_eff") or t["entry"]),
                              float(exit_eff or exit_price), fee)
            await self.db[TRIALS_COLL].update_one({"id": t["id"]}, {"$set": {
                "status": "closed", "result": res, "exit_price": exit_price,
                "exit_eff": float(exit_eff or exit_price), "exit_exec": exit_exec,
                "net_pnl_pct": pnl, "closed_at": _now_iso()}})

    # ---------------- Promotion & Rollback (Audit 3.3) ----------------

    async def _notify(self, text: str):
        try:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "governance",
                "text": text, "ts": _now_iso()})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Policy-Lab Chat-Notify: {e}")
        try:
            from core import state
            from services import notifications
            await notifications.telegram_notify(self.db, state.telegram, "policy_lab", text)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Policy-Lab Telegram: {e}")

    async def _champion_rs(self, since_iso: str) -> List[float]:
        """Netto-R (Geld, Audit 2.3) der Champion-Trades seit `since_iso`."""
        from services.ml_gate import money_r
        rows = await self.db.auto_trades.find(
            {"strategy_id": "ai_trader", "status": "closed",
             "closed_at": {"$gte": str(since_iso or "")}},
            {"_id": 0, "realized_pnl": 1, "risk_usdt": 1, "risk": 1, "qty": 1,
             "closed_at": 1}).limit(1000).to_list(1000)
        return [r for r in (money_r(t) for t in rows) if r is not None]

    async def promotion_report(self) -> Dict:
        """Promotion-Prüfung (3.3): Kandidat-Trials vs. Champion-Trades – beide NUR
        aus dem Zeitraum nach Kandidaten-Erzeugung, beide als R-Multiple."""
        cand = self.candidate
        if not cand:
            return {"status": "no_candidate"}
        rows = await self.db[TRIALS_COLL].find(
            {"candidate_id": cand["id"], "status": "closed"},
            {"_id": 0}).limit(1000).to_list(1000)
        cand_rs = [r for r in (policy_promotion.trial_r(t) for t in rows) if r is not None]
        champ_rs = await self._champion_rs(str(cand.get("created_at") or ""))
        rep = policy_promotion.promotion_check(cand_rs, champ_rs, self.promotion_cfg)
        return {"status": "ok", "candidate_id": cand["id"],
                "candidate_status": cand.get("status"), **rep}

    async def _check_promotion(self):
        cand = self.candidate
        if not cand or cand.get("status") != "active":
            return
        rep = await self.promotion_report()
        if rep.get("status") != "ok":
            return
        await self.db[CAND_COLL].update_one({"id": cand["id"]}, {"$set": {
            "last_report": rep, "last_report_at": _now_iso()}})
        if not rep.get("promote"):
            return
        await self.db[CAND_COLL].update_one({"id": cand["id"]}, {"$set": {
            "status": "promotion_ready", "promotion_ready_at": _now_iso()}})
        self.candidate = {**cand, "status": "promotion_ready", "last_report": rep}
        m = rep.get("metrics") or {}
        await self._notify(
            f"🏁 Policy-Kandidat „{cand['name']}“ erfüllt die Promotion-Kriterien "
            f"(n={m.get('n_cand')}, ΔR-Mittel={m.get('mean_diff')}, Bootstrap-Untergrenze="
            f"{m.get('bootstrap_lower')}). Die Promotion wartet auf DEINE Freigabe "
            "(POST /api/policy-lab/promote).")

    async def promote(self, force: bool = False) -> Dict:
        """Kandidat wird Champion: Overrides in die Engine-Config, Suffix in den
        MasterPrompt. Snapshot für den automatischen Rollback wird gespeichert."""
        cand = self.candidate
        if not cand:
            return {"status": "rejected", "reason": "Kein aktiver Kandidat"}
        if cand.get("status") == "promoted":
            return {"status": "rejected", "reason": "Kandidat ist bereits promoted"}
        if cand.get("status") != "promotion_ready" and not force:
            return {"status": "rejected",
                    "reason": "Promotion-Kriterien noch nicht erfüllt "
                              "(force=true zum Erzwingen)"}
        rep = await self.promotion_report()
        overrides = cand.get("config_overrides") or {}
        prev_cfg = {k: self.engine.config.get(k) for k in overrides}
        if overrides:
            await self.engine.update_config(dict(overrides))
        mp_version_before = None
        suffix = str(cand.get("system_suffix") or "")
        if suffix:
            from services.ai_master_prompt import master_prompt
            mp_version_before = master_prompt.version
            await master_prompt.save(
                text=(master_prompt.text or "")
                + f"\n\n[Policy-Promotion {cand['id']}] {suffix}",
                editor=f"policy_lab({cand['id']})")
        upd = {"status": "promoted", "promoted_at": _now_iso(),
               "promotion_report": rep,
               "promotion_snapshot": {"prev_config": prev_cfg,
                                      "master_prompt_version_before": mp_version_before},
               "rollback_baseline_r": float(((rep.get("metrics") or {})
                                             .get("champ_mean_r")) or 0)}
        await self.db[CAND_COLL].update_one({"id": cand["id"]}, {"$set": upd})
        self.candidate = {**cand, **upd}
        await self._notify(
            f"✅ Policy-Promotion: „{cand['name']}“ ist jetzt Champion. Das Rollback-Fenster "
            f"({self.promotion_cfg['rollback_window_trades']} Trades) wird überwacht – bei "
            "Verschlechterung erfolgt die Rücknahme automatisch.")
        return {"status": "ok", "candidate": self.candidate}

    async def _check_rollback(self):
        """Automatische Rücknahme bei Verschlechterung nach Promotion (Rollback-Fenster)."""
        cand = self.candidate
        if not cand or cand.get("status") != "promoted":
            return
        post_rs = await self._champion_rs(str(cand.get("promoted_at") or ""))
        res = policy_promotion.rollback_check(
            post_rs, float(cand.get("rollback_baseline_r") or 0), self.promotion_cfg)
        if res.get("rollback"):
            await self.rollback(reason=f"AUTO: {res['reason']}")
        elif len(post_rs) >= int(self.promotion_cfg["rollback_window_trades"]):
            await self.db[CAND_COLL].update_one({"id": cand["id"]}, {"$set": {
                "status": "promoted_final", "rollback_watch_done_at": _now_iso()}})
            self.candidate = None
            await self._notify(f"🏆 Policy „{cand['name']}“ hat das Rollback-Fenster ohne "
                               "Verschlechterung bestanden – Promotion ist final.")

    async def rollback(self, reason: str = "") -> Dict:
        """Promotion zurücknehmen: Config- und MasterPrompt-Stand wiederherstellen."""
        cand = self.candidate
        if not cand or cand.get("status") != "promoted":
            return {"status": "rejected", "reason": "Kein promoteter Kandidat aktiv"}
        snap = cand.get("promotion_snapshot") or {}
        prev_cfg = snap.get("prev_config") or {}
        if prev_cfg:
            await self.engine.update_config(dict(prev_cfg))
        mp_v = snap.get("master_prompt_version_before")
        if mp_v:
            from services.ai_master_prompt import master_prompt
            await master_prompt.restore(int(mp_v))
        await self.db[CAND_COLL].update_one({"id": cand["id"]}, {"$set": {
            "status": "rolled_back", "rolled_back_at": _now_iso(),
            "rollback_reason": str(reason or "")[:300]}})
        self.candidate = None
        await self._notify(f"↩️ Policy-Rollback: „{cand['name']}“ wurde zurückgenommen"
                           f"{' – ' + reason if reason else ''}.")
        return {"status": "ok", "id": cand["id"]}

    # ---------------- Status/API ----------------

    async def candidate_stats(self, cid: Optional[str] = None) -> Dict:
        cid = cid or (self.candidate or {}).get("id")
        if not cid:
            return trial_stats([])
        rows = await self.db[TRIALS_COLL].find(
            {"candidate_id": cid}, {"_id": 0}).limit(1000).to_list(1000)
        return trial_stats(rows)

    async def status(self) -> Dict:
        st = {"settings": dict(self.settings),
              "promotion": dict(self.promotion_cfg),
              "candidate": self.candidate,
              "cycle_n": self._cycle_n, "last_shadow": self.last_shadow,
              "last_error": self.last_error}
        if self.db is not None and self.candidate:
            st["stats"] = await self.candidate_stats()
        return st

    async def recent_trials(self, limit: int = 50) -> List[Dict]:
        rows = await self.db[TRIALS_COLL].find({}, {"_id": 0}) \
            .sort("opened_at", -1).limit(max(1, min(200, limit))).to_list(200)
        return rows


policy_lab = PolicyLab()
