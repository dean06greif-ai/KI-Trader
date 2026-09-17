"""Trade-Aktivitäts-Wächter: adaptive Lockerung/Straffung der KI-Schwellen.

Problem (09/2026): Der KI Trader machte kaum noch Trades – die über die Zeit
verschärften Schwellen (min_confidence, Cooldowns) strangulierten die Aktivität
und damit auch die Lernbasis (wenige Trades = wenig Setup-Verbesserung).

Lösung: EIN Regelkreis statt manueller Eingriffe.
  * Ist die Trade-Aktivität (alle KI-Trades inkl. Paper/Datensammlung) im
    Fenster unter dem Ziel, wird EINE Stufe gelockert: min_confidence und
    collection_min_confidence sinken um conf_step, Cooldowns um cooldown_step.
  * Erholt sich die Aktivität deutlich (>= 1.5x Ziel), wird stufenweise in
    Richtung der gemerkten Basiswerte zurückgestrafft.
  * Leitplanken: nie unter die Autonomie-Untergrenzen (tune_conf_min bzw.
    harte Floors), max. max_steps Stufen, min. 6h Abstand zwischen Schritten.
  * Jede Änderung wird transparent in den KI-Chat (Rolle governance) gepostet.

Zustand in settings.activity_guard; Anwendung über engine.update_config
(bestehende Klemmung/Persistenz). Tick läuft im run_loop der Engine –
und damit NICHT auf Preview-Instanzen (local_engine_disabled-Guard).
"""
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

STATE_ID = "activity_guard"
DEFAULTS: Dict = {
    "enabled": True,
    "target_trades_per_day": 3,   # Ziel: KI-Trades/Tag (inkl. Paper/Sammel)
    "window_hours": 48,           # Messfenster
    "max_steps": 3,               # max. Lockerungs-Stufen
    "conf_step": 2,               # Konfidenz-Schritt je Stufe (Punkte)
    "cooldown_step": 5,           # Cooldown-Schritt je Stufe (Minuten)
    "min_gap_hours": 6,           # Mindestabstand zwischen Anpassungen
}
CONF_FLOOR = 50                   # harte Untergrenze min_confidence
COLL_CONF_FLOOR = 50              # harte Untergrenze collection_min_confidence
COOLDOWN_FLOOR = 10               # harte Untergrenze Cooldowns (Minuten)
CHECK_EVERY_S = 1800
ADJUST_KEYS = ("min_confidence", "collection_min_confidence",
               "cooldown_min", "collection_cooldown_min")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize(raw: Optional[Dict]) -> Dict:
    raw = raw or {}
    cfg = dict(DEFAULTS)
    cfg["enabled"] = bool(raw.get("enabled", True))
    for key, lo, hi in (("target_trades_per_day", 1, 50), ("window_hours", 12, 168),
                        ("max_steps", 1, 5), ("conf_step", 1, 5),
                        ("cooldown_step", 1, 20), ("min_gap_hours", 1, 48)):
        try:
            cfg[key] = max(lo, min(hi, int(raw.get(key, DEFAULTS[key]))))
        except (TypeError, ValueError):
            pass
    for k in ("steps", "baseline", "last_adjust_at", "last_check", "history"):
        if raw.get(k) is not None:
            cfg[k] = raw[k]
    return cfg


def loosen_values(cur: Dict, cfg: Dict, tune_conf_min: int) -> Dict:
    """Eine Lockerungs-Stufe berechnen (rein, testbar)."""
    conf_floor = max(CONF_FLOOR, int(tune_conf_min or CONF_FLOOR))
    out = {}
    v = int(cur.get("min_confidence") or 65) - cfg["conf_step"]
    out["min_confidence"] = max(conf_floor, v)
    v = int(cur.get("collection_min_confidence") or 60) - cfg["conf_step"]
    out["collection_min_confidence"] = max(COLL_CONF_FLOOR, v)
    v = int(cur.get("cooldown_min") or 45) - cfg["cooldown_step"]
    out["cooldown_min"] = max(COOLDOWN_FLOOR, v)
    v = int(cur.get("collection_cooldown_min") or 30) - cfg["cooldown_step"]
    out["collection_cooldown_min"] = max(COOLDOWN_FLOOR, v)
    return {k: v for k, v in out.items() if v != cur.get(k)}


def tighten_values(cur: Dict, baseline: Dict, cfg: Dict) -> Dict:
    """Eine Stufe zurück Richtung Basiswerte (nie über die Basis hinaus)."""
    out = {}
    for key, step in (("min_confidence", cfg["conf_step"]),
                      ("collection_min_confidence", cfg["conf_step"]),
                      ("cooldown_min", cfg["cooldown_step"]),
                      ("collection_cooldown_min", cfg["cooldown_step"])):
        base = baseline.get(key)
        if base is None:
            continue
        v = min(int(base), int(cur.get(key) or base) + step)
        if v != cur.get(key):
            out[key] = v
    return out


class ActivityGuard:
    def __init__(self):
        self.engine = None
        self._next_check = 0.0
        self.last_result: Optional[Dict] = None

    def setup(self, engine):
        self.engine = engine

    @property
    def db(self):
        return self.engine.db if self.engine else None

    async def _load(self) -> Dict:
        doc = await self.db.settings.find_one({"_id": STATE_ID}) or {}
        return normalize(doc)

    async def _save(self, state: Dict) -> None:
        doc = {k: v for k, v in state.items() if k != "_id"}
        await self.db.settings.update_one({"_id": STATE_ID}, {"$set": doc}, upsert=True)

    async def trades_per_day(self, window_hours: int) -> float:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
        n = await self.db.auto_trades.count_documents(
            {"strategy_id": "ai_trader", "opened_at": {"$gte": cutoff}})
        return round(n / (window_hours / 24.0), 2)

    async def _post_chat(self, text: str) -> None:
        try:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "governance",
                "text": f"⚖️ Aktivitäts-Wächter: {text}", "ts": _now_iso()})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"ActivityGuard Chat-Post: {e}")

    async def check(self, manual: bool = False) -> Dict:
        state = await self._load()
        if not state["enabled"] and not manual:
            return {"status": "disabled"}
        rate = await self.trades_per_day(state["window_hours"])
        target = state["target_trades_per_day"]
        steps = int(state.get("steps") or 0)
        cur = {k: self.engine.config.get(k) for k in ADJUST_KEYS}
        result: Dict = {"status": "ok", "rate": rate, "target": target,
                        "steps": steps, "action": "none", "at": _now_iso()}
        last_adj = state.get("last_adjust_at")
        gap_ok = True
        if last_adj:
            try:
                dt = datetime.fromisoformat(str(last_adj).replace("Z", "+00:00"))
                gap_ok = (datetime.now(timezone.utc) - dt) >= timedelta(hours=state["min_gap_hours"])
            except ValueError:
                pass
        if rate < target and steps < state["max_steps"] and gap_ok:
            changes = loosen_values(cur, state, self.engine.config.get("tune_conf_min", 55))
            if changes:
                if not state.get("baseline"):
                    state["baseline"] = dict(cur)
                await self.engine.update_config(changes)
                state["steps"] = steps + 1
                state["last_adjust_at"] = _now_iso()
                result.update({"action": "loosened", "changes": changes, "steps": state["steps"]})
                await self._post_chat(
                    f"nur {rate} KI-Trades/Tag (Ziel {target}) – Stufe {state['steps']}/"
                    f"{state['max_steps']} gelockert: "
                    + ", ".join(f"{k} {cur.get(k)}→{v}" for k, v in changes.items())
                    + ". Mehr Trades = mehr Lernbasis; Rückstraffung automatisch bei Erholung.")
        elif rate >= target * 1.5 and steps > 0 and gap_ok and state.get("baseline"):
            changes = tighten_values(cur, state["baseline"], state)
            if changes:
                await self.engine.update_config(changes)
                state["steps"] = steps - 1
                state["last_adjust_at"] = _now_iso()
                if state["steps"] == 0:
                    state["baseline"] = None
                result.update({"action": "tightened", "changes": changes, "steps": state["steps"]})
                await self._post_chat(
                    f"Aktivität erholt ({rate}/Tag, Ziel {target}) – eine Stufe zurückgestrafft: "
                    + ", ".join(f"{k} {cur.get(k)}→{v}" for k, v in changes.items()))
        state["last_check"] = {"at": result["at"], "rate": rate, "action": result["action"]}
        if result["action"] != "none":
            hist = list(state.get("history") or [])
            hist = ([{k: result[k] for k in ("at", "rate", "action", "changes", "steps")}] + hist)[:20]
            state["history"] = hist
        await self._save(state)
        self.last_result = result
        return result

    async def status(self) -> Dict:
        state = await self._load()
        rate = None
        try:
            rate = await self.trades_per_day(state["window_hours"])
        except Exception:  # noqa: BLE001
            pass
        cur = {k: self.engine.config.get(k) for k in ADJUST_KEYS} if self.engine else {}
        return {**{k: state.get(k) for k in list(DEFAULTS) + ["steps", "baseline",
                                                              "last_adjust_at", "last_check", "history"]},
                "current_rate": rate, "current_values": cur,
                "floors": {"min_confidence": max(CONF_FLOOR, int((self.engine.config.get("tune_conf_min") if self.engine else 55) or 55)),
                           "collection_min_confidence": COLL_CONF_FLOOR,
                           "cooldown_min": COOLDOWN_FLOOR}}

    async def save_config(self, raw: Dict) -> Dict:
        state = await self._load()
        merged = normalize({**state, **{k: v for k, v in (raw or {}).items() if k in DEFAULTS}})
        for k in ("steps", "baseline", "last_adjust_at", "last_check", "history"):
            if state.get(k) is not None:
                merged[k] = state[k]
        await self._save(merged)
        return await self.status()

    async def tick(self):
        if self.engine is None or self.db is None:
            return
        now = time.time()
        if now < self._next_check:
            return
        self._next_check = now + CHECK_EVERY_S
        try:
            await self.check()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"ActivityGuard check: {e}")


activity_guard = ActivityGuard()
