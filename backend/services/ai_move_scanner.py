"""Bewegungs-Scanner: starke Kursbewegungen UNABHÄNGIG von den Setups erkennen,
Ursache analysieren und System-Lücken schließen.

Ablauf je erkannter Bewegung (Move):
  1. Erkennung datengetrieben & LLM-frei aus den Markt-Beobachter-Features:
     |60m-Änderung| >= max(Klassen-Schwelle, vol_mult x gemessene 60m-Vola).
  2. Abgleich: Hat der KI Trader die Bewegung erwischt? (offene/junge
     auto_trades bzw. Nicht-HOLD-Entscheidungen im Vorfeld-Fenster)
  2b. Regelbasierte Vor-Einordnung OHNE LLM (classify): erwischt / Detektor
     eines Setups hat gefeuert (Ausführungs-Lücke) / News-Impuls (momentum_news)
     -> dokumentiert, KEIN LLM. Nur echte Setup-Lücken (kein Setup, keine News)
     gehen an die KI-Analyse.
  3. KI-Analyse (research_analyst, Tagesbudget): WARUM ist die Bewegung
     passiert (News-Kontext), hätte ein Playbook-Setup gepasst, wurde sie
     verpasst – und was folgt daraus:
       * action "revise":    bestehendes Setup überarbeiten (ai_playbook.revise_setup
                             – greift nur bei rückgestuften/inaktiven Setups, sonst
                             wird der Vorschlag als Hinweis in den Chat gepostet)
       * action "new_setup": neues Datensammel-Setup vorschlagen
                             (ai_playbook.propose_custom_setup – läuft automatisch
                             als Paper-Datensammlung durch das Reife-Gate)
       * action "none":      dokumentieren (Lücke ohne belastbares Muster)
  4. Ergebnis -> Collection ai_move_events + KI-Chat (Rolle governance);
     Retention 30 Tage (services/retention.py).

Kosten-Leitplanken: Symbol-Cooldown, max. LLM-Analysen/Tag, Erkennung selbst
kostet kein LLM-Budget. Tick läuft im Engine-run_loop (nicht auf Previews).
"""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_ID = "move_scanner"
CHECK_EVERY_S = 300
DEFAULTS: Dict = {
    "enabled": True,
    "vol_mult": 4.0,            # Move >= vol_mult x 60m-Vola des Symbols
    "cooldown_min": 240,        # je Symbol
    "max_llm_per_day": 12,      # LLM-Analysen-Tagesbudget
}
# Mindest-Schwelle |60m-Änderung| je Anlageklasse (Prozent)
CLASS_MIN_MOVE = {"crypto": 1.2, "indices": 0.7, "resources": 0.8, "forex": 0.35}
LOOKBACK_CAUGHT_MIN = 120       # Fenster für "haben wir die Bewegung erwischt?"

MOVE_SYSTEM = (
    "Du bist der 'Forschungs-Analyst' im KI-Team einer Daytrading-Plattform. Eine STARKE "
    "Kursbewegung wurde datengetrieben erkannt. Deine Aufgabe: Ursache erklären, prüfen ob "
    "ein Playbook-Setup die Bewegung hätte handeln müssen, und die System-Lücke schließen. "
    "Sei streng: schlage NUR dann eine Setup-Änderung oder ein neues Setup vor, wenn ein "
    "WIEDERHOLBARES, regelbasiertes Muster erkennbar ist – einmalige News-Spikes sind KEIN "
    "Setup. Alle Texte auf DEUTSCH. Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown:\n"
    '{"cause": "1-2 Sätze: wahrscheinliche Ursache der Bewegung", '
    '"setup_match": "Setup-ID aus dem Playbook, die gepasst hätte, oder null", '
    '"missed": true|false, '
    '"missed_reason": "warum wurde sie verpasst (Filter/Schwelle/Setup-Lücke) oder null", '
    '"action": "none|revise|new_setup", '
    '"setup_id": "bei revise: bestehende Setup-ID; bei new_setup: neue snake_case-ID (3-24 Zeichen) oder null", '
    '"desc": "bei revise/new_setup: klare Regelbeschreibung (Entry/Exit/Filter, min. 20 Zeichen) oder null", '
    '"reason": "1 Satz Begründung der Aktion"}'
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_strong_move(feats: Dict, asset_class: str, vol_mult: float) -> bool:
    """Erkennungsregel (rein, testbar)."""
    if not feats or feats.get("market_closed"):
        return False
    chg = abs(float(feats.get("change_60m_pct") or 0))
    vola = float(feats.get("volatility_pct") or 0)
    threshold = max(CLASS_MIN_MOVE.get(asset_class, 1.2), vol_mult * vola)
    return chg >= threshold


def classify(caught: bool, detector_hits: List[Dict], news_hit: bool,
             direction: str) -> Dict:
    """Regelbasierte Vor-Einordnung einer Bewegung (rein, ohne LLM).

    Liefert cause/missed/missed_reason/setup_match/needs_llm:
      * erwischt                        -> dokumentieren, kein LLM
      * Detektor eines Setups hat gefeuert, aber kein Trade
                                        -> Lücke liegt in der AUSFÜHRUNG (Gate/
                                           Konfidenz/Cooldown), nicht im Setup -> kein LLM
      * News im Vorfeld                 -> momentum_news-Fall (News-Spike ist KEIN neues Setup)
                                        -> kein LLM
      * weder Setup noch News           -> echte Setup-Lücke -> LLM (Ursache + ggf. new_setup)
    """
    same_dir = [h for h in detector_hits if str(h.get("side")) == direction]
    if caught:
        return {"cause": "Bewegung vom KI Trader gehandelt", "missed": False,
                "missed_reason": None, "setup_match": (same_dir[0]["setup"] if same_dir else None),
                "kind": "caught", "needs_llm": False}
    if same_dir:
        setups = sorted({h["setup"] for h in same_dir})
        return {"cause": f"Regelbasierte Detektoren hätten gefeuert: {', '.join(setups)}",
                "missed": True,
                "missed_reason": (f"Setup(s) {', '.join(setups)} haben die Bewegung erkannt – "
                                  "kein Trade: Live-Gate/Konfidenz/Cooldown oder Setup nicht "
                                  "live-reif (Paper-Sammeltrade via Setup-Trigger)"),
                "setup_match": setups[0], "kind": "execution_gap", "needs_llm": False}
    if news_hit:
        return {"cause": "News-getriebener Impuls (News-Wächter meldete Ereignis im Vorfeld)",
                "missed": True,
                "missed_reason": "News-Spike ohne Kursmuster – Fall für momentum_news "
                                 "(Impuls-Detektor des Setup-Triggers), kein neues Setup",
                "setup_match": "momentum_news", "kind": "news", "needs_llm": False}
    return {"cause": None, "missed": True, "missed_reason": None, "setup_match": None,
            "kind": "setup_gap", "needs_llm": True}


def normalize(raw: Optional[Dict]) -> Dict:
    raw = raw or {}
    cfg = dict(DEFAULTS)
    cfg["enabled"] = bool(raw.get("enabled", True))
    for key, lo, hi in (("vol_mult", 2.0, 10.0), ("cooldown_min", 30, 1440),
                        ("max_llm_per_day", 1, 50)):
        try:
            cfg[key] = max(lo, min(hi, float(raw.get(key, DEFAULTS[key]))))
        except (TypeError, ValueError):
            pass
    cfg["cooldown_min"] = int(cfg["cooldown_min"])
    cfg["max_llm_per_day"] = int(cfg["max_llm_per_day"])
    return cfg


class MoveScanner:
    ROLE = "research_analyst"

    def __init__(self):
        self.engine = None
        self._next_check = 0.0
        self._cooldowns: Dict[str, float] = {}     # symbol -> monotonic bis
        self._llm_day = ""
        self._llm_used = 0
        self.last_run: Optional[str] = None
        self.last_error: Optional[str] = None

    def setup(self, engine):
        self.engine = engine

    @property
    def db(self):
        return self.engine.db if self.engine else None

    async def _cfg(self) -> Dict:
        doc = await self.db.settings.find_one({"_id": STATE_ID}) or {}
        return normalize(doc)

    async def save_config(self, raw: Dict) -> Dict:
        cur = await self._cfg()
        merged = normalize({**cur, **{k: v for k, v in (raw or {}).items() if k in DEFAULTS}})
        await self.db.settings.update_one({"_id": STATE_ID}, {"$set": merged}, upsert=True)
        return merged

    def _llm_budget_left(self, max_per_day: int) -> bool:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self._llm_day:
            self._llm_day, self._llm_used = day, 0
        return self._llm_used < max_per_day

    async def _caught_check(self, symbol: str) -> Dict:
        """Hat der KI Trader die Bewegung gehandelt (oder bewusst entschieden)?"""
        cutoff = (datetime.now(timezone.utc)
                  - timedelta(minutes=LOOKBACK_CAUGHT_MIN)).isoformat()
        trade = await self.db.auto_trades.find_one(
            {"strategy_id": "ai_trader", "symbol": symbol, "opened_at": {"$gte": cutoff}},
            {"_id": 0, "id": 1, "side": 1, "setup": 1, "mode": 1, "opened_at": 1})
        decision = await self.db.ai_decisions.find_one(
            {"symbol": symbol, "ts": {"$gte": cutoff}, "action": {"$in": ["LONG", "SHORT"]}},
            {"_id": 0, "action": 1, "confidence": 1, "setup": 1, "ts": 1})
        return {"caught": bool(trade), "trade": trade, "decision": decision}

    async def _analyze(self, event: Dict) -> Optional[Dict]:
        """LLM-Analyse eines Moves; wendet revise/new_setup direkt an."""
        from services import ai_playbook
        feats = event.get("features") or {}
        caught = event.get("caught_info") or {}
        setups = ai_playbook.all_setups()
        setup_lines = "\n".join(f"- {sid}: {desc[:120]}" for sid, desc in list(setups.items())[:30])
        news_txt = ""
        try:
            from services.ai_news_watcher import news_watcher
            news_txt = await news_watcher.context_text(limit=5)
        except Exception:  # noqa: BLE001
            pass
        direction = "AUFWÄRTS" if float(feats.get("change_60m_pct") or 0) > 0 else "ABWÄRTS"
        caught_txt = "JA – " + str(caught.get("trade")) if caught.get("caught") else \
            ("NEIN (aber Entscheidung ohne Trade: " + str(caught.get("decision")) + ")"
             if caught.get("decision") else "NEIN – weder Trade noch LONG/SHORT-Entscheidung")
        prompt = (
            f"=== ERKANNTE STARKE BEWEGUNG ===\n"
            f"Symbol: {event['symbol']} ({event.get('asset_class')}) · Richtung: {direction}\n"
            f"60m-Änderung: {feats.get('change_60m_pct')}% · 60m-Vola: {feats.get('volatility_pct')}% · "
            f"Volumen x{feats.get('volume_ratio')} · RSI {feats.get('rsi')} · Regime {feats.get('regime')} · "
            f"Range-Pos {feats.get('range_pos')}%\n"
            f"Regelbasierte Detektoren im Vorfeld: KEIN Setup hat gefeuert, keine News gemeldet "
            f"-> mögliche Setup-Lücke in der Anlageklasse.\n\n"
            f"=== HAT DER KI TRADER DIE BEWEGUNG ERWISCHT? ===\n{caught_txt}\n\n"
            + (f"{news_txt}\n\n" if news_txt else "")
            + f"=== PLAYBOOK-SETUPS (IDs für setup_match/revise) ===\n{setup_lines}\n\n"
            + ai_playbook.alias_check_text() + "\n\n"
            "Analysiere Ursache und System-Lücke als JSON."
        )
        text, provider, model = await self.engine.generate_for_role(
            self.ROLE, prompt, MOVE_SYSTEM, temperature=0.3)
        data = self.engine._parse_json(text)
        if not isinstance(data, dict) or not data.get("cause"):
            return None
        analysis = {
            "cause": str(data.get("cause"))[:400],
            "setup_match": (str(data.get("setup_match"))[:30]
                            if data.get("setup_match") else None),
            "missed": bool(data.get("missed")),
            "missed_reason": (str(data.get("missed_reason"))[:300]
                              if data.get("missed_reason") else None),
            "action": data.get("action") if data.get("action") in ("none", "revise", "new_setup") else "none",
            "setup_id": (str(data.get("setup_id"))[:30] if data.get("setup_id") else None),
            "desc": (str(data.get("desc"))[:300] if data.get("desc") else None),
            "reason": str(data.get("reason") or "")[:200],
            "model": f"{provider}/{model}",
        }
        analysis["action_result"] = await self._apply_action(event, analysis)
        return analysis

    async def _apply_action(self, event: Dict, analysis: Dict) -> Optional[Dict]:
        from services import ai_playbook
        action, sid, desc = analysis["action"], analysis.get("setup_id"), analysis.get("desc")
        if action == "none" or not sid or not desc:
            return None
        try:
            if action == "new_setup":
                res = await ai_playbook.propose_custom_setup(
                    self.db, sid, desc, source="move_scanner")
                return {"kind": "new_setup", **res}
            if action == "revise":
                res = await ai_playbook.revise_setup(
                    self.db, event.get("asset_class") or "crypto", sid, desc,
                    reason=f"Bewegungs-Scanner: {analysis.get('reason') or 'verpasste Bewegung'}",
                    source="move_scanner")
                return {"kind": "revise", **res}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Move-Scanner Aktion {action}/{sid}: {e}")
            return {"kind": action, "status": "error", "reason": str(e)[:150]}
        return None

    async def _post_chat(self, event: Dict, analysis: Optional[Dict]) -> None:
        feats = event.get("features") or {}
        caught = (event.get("caught_info") or {}).get("caught")
        lines = [f"📈 Bewegungs-Scanner: {event['symbol']} "
                 f"{float(feats.get('change_60m_pct') or 0):+.2f}% in 60m "
                 f"(Vol x{feats.get('volume_ratio')}, Regime {feats.get('regime')}) – "
                 + ("vom KI Trader ERWISCHT ✅" if caught else "NICHT gehandelt ⚠️")]
        if analysis:
            lines.append(f"Ursache: {analysis['cause']}")
            if analysis.get("missed") and analysis.get("missed_reason"):
                lines.append(f"Verpasst weil: {analysis['missed_reason']}")
            if analysis.get("setup_match"):
                lines.append(f"Passendes Setup: {analysis['setup_match']}")
            ar = analysis.get("action_result")
            if ar:
                if ar.get("status") == "ok":
                    lines.append(("Neues Datensammel-Setup angelegt: " if ar["kind"] == "new_setup"
                                  else "Setup-Revision gestartet: ") + str(analysis.get("setup_id")))
                else:
                    lines.append(f"Vorschlag ({ar.get('kind')}, {analysis.get('setup_id')}) nicht "
                                 f"angewendet: {ar.get('reason')} – als Hinweis dokumentiert.")
            elif analysis.get("reason"):
                lines.append(f"Einschätzung: {analysis['reason']}")
        try:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "governance",
                "text": "\n".join(lines)[:2000], "ts": _now_iso()})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Move-Scanner Chat-Post: {e}")

    async def scan(self, manual: bool = False, symbol: Optional[str] = None) -> Dict:
        """Ein Scan-Durchlauf: Moves erkennen, analysieren, dokumentieren."""
        from services import setup_asset_class as ac
        from services.ai_market_observer import market_observer
        cfg = await self._cfg()
        if not cfg["enabled"] and not manual:
            return {"status": "disabled"}
        found: List[Dict] = []
        now_mono = time.monotonic()
        snaps = market_observer.snapshots
        symbols = [symbol] if symbol else list(snaps.keys())
        for sym in symbols:
            feats = (snaps.get(sym) or {}).get("features") or {}
            cls = ac.asset_class_of(sym)
            if not manual and now_mono < self._cooldowns.get(sym, 0):
                continue
            if not is_strong_move(feats, cls, cfg["vol_mult"]):
                continue
            self._cooldowns[sym] = now_mono + cfg["cooldown_min"] * 60
            caught_info = await self._caught_check(sym)
            direction = "LONG" if float(feats.get("change_60m_pct") or 0) > 0 else "SHORT"
            hits, news_hit = [], False
            try:
                from services.setup_trigger import setup_trigger
                hits = setup_trigger.detector_hits(sym, window_min=LOOKBACK_CAUGHT_MIN)
                news_hit = await setup_trigger._news_hit(sym)
            except Exception as e:  # noqa: BLE001
                logger.debug(f"Move-Scanner Detektor-Abgleich {sym}: {e}")
            pre = classify(caught_info["caught"], hits, news_hit, direction)
            event = {"id": str(uuid.uuid4()), "ts": _now_iso(), "symbol": sym,
                     "asset_class": cls, "features": feats, "caught_info": caught_info,
                     "caught": caught_info["caught"], "detector_hits": hits[:10],
                     "news_hit": news_hit, "kind": pre["kind"]}
            analysis = None
            if not pre["needs_llm"]:
                # Regelbasiert erklärt -> kein LLM-Budget verbrauchen
                analysis = {"cause": pre["cause"], "setup_match": pre["setup_match"],
                            "missed": pre["missed"], "missed_reason": pre["missed_reason"],
                            "action": "none", "setup_id": None, "desc": None,
                            "reason": "regelbasiert eingeordnet (ohne LLM)",
                            "model": "rules", "action_result": None}
            elif self.engine and self.engine.key \
                    and self._llm_budget_left(cfg["max_llm_per_day"]):
                self._llm_used += 1
                try:
                    analysis = await self._analyze(event)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Move-Scanner Analyse {sym}: {e}")
            event["analysis"] = analysis
            try:
                await self.db.ai_move_events.insert_one(dict(event))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Move-Event speichern: {e}")
            await self._post_chat(event, analysis)
            event.pop("_id", None)
            found.append(event)
            logger.info(f"Move-Scanner: {sym} {feats.get('change_60m_pct')}% "
                        f"(caught={caught_info['caught']}, analyse={'ja' if analysis else 'nein'})")
        self.last_run = _now_iso()
        return {"status": "ok", "moves": len(found), "events": found}

    async def status(self) -> Dict:
        cfg = await self._cfg()
        recent = await self.db.ai_move_events.find({}, {"_id": 0}) \
            .sort("ts", -1).limit(20).to_list(20)
        return {**cfg, "last_run": self.last_run, "last_error": self.last_error,
                "llm_used_today": self._llm_used,
                "class_min_move": CLASS_MIN_MOVE, "recent": recent}

    async def tick(self):
        if self.engine is None or self.db is None:
            return
        now = time.time()
        if now < self._next_check:
            return
        self._next_check = now + CHECK_EVERY_S
        try:
            await self.scan()
            self.last_error = None
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:200]
            logger.warning(f"Move-Scanner Tick: {e}")


move_scanner = MoveScanner()
