"""Setup-Review: automatische Überarbeitung von Setups, die (a) länger nicht
auslösen (INAKTIV laut Aktivitäts-Wächter) oder (b) chronisch negativ sind
(nie sinnvoll/positiv – über alle Parameter-Versionen hinweg).

Ablauf (einmal täglich aus dem Engine-Loop, ai_engine.run_loop):
  1. Je Anlageklasse: chronisch negative Setups -> in `live_blocked` als
     `suspended` (Live-Einstiege pausiert, längerer Re-Test), Feed + Telegram.
     Es wird NICHTS gelöscht (Trader-Entscheid) – nur deaktiviert + überarbeitet.
  2. Inaktive (>= INACTIVE_MIN_AGE_DAYS) und chronische Setups: EIN kompakter
     LLM-Aufruf (Rolle research_analyst) -> neue Regelbeschreibung + realis-
     tisches trade_target -> ai_playbook.revise_setup (bestehender Revisions-
     Pfad: Validierung startet neu, Cooldown REVISION_COOLDOWN_DAYS gilt).
Token-Bremse: höchstens MAX_LLM_PER_DAY Revisionen pro Tag, Prompt < ~700 Tokens.
Alle Regeln sind reine Funktionen (testbar); Zustand in settings.setup_review_state.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from services import setup_asset_class as ac

logger = logging.getLogger(__name__)

STATE_ID = "setup_review_state"
ROLE = "research_analyst"
LOOKBACK_DAYS = 60
CHRONIC_MIN_TRADES = 12
CHRONIC_MAX_WINRATE = 45.0
VERSION_MIN_TRADES = 5
INACTIVE_MIN_AGE_DAYS = 2
MAX_LLM_PER_DAY = 3
SUSPEND_DAYS = 28
RUN_EVERY_H = 24

SYSTEM = (
    "Du bist der Forschungs-Analyst eines KI-Daytraders. Ein Trading-Setup ist entweder INAKTIV "
    "(löst kaum aus) oder CHRONISCH NEGATIV (über alle Versionen verlustreich). Überarbeite die "
    "Regelbeschreibung grundlegend, aber knapp: klarer Trigger, Invalidierung (SL), Ziel, ggf. Timeframe/"
    "Session – bei INAKTIV lockerer/häufiger, bei CHRONISCH NEGATIV eine andere Logik (Diagnose nutzen). "
    "Setze ein realistisches trade_target (Trades/Woche für die GESAMTE Anlageklasse – wenige Assets = "
    "kleineres Ziel). Antworte NUR mit JSON: {\"desc\": \"neue Regel (max. 300 Zeichen, deutsch)\", "
    "\"reason\": \"Befund -> Änderung (max. 200 Zeichen)\", \"trade_target\": <int 1-50>}"
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts) -> Optional[datetime]:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def chronic_reason(stats: Optional[Dict], versions: Optional[List[Dict]]) -> Optional[str]:
    """Chronisch negativ (rein): genug Trades, PnL < 0, Winrate unter Schwelle und
    KEINE Parameter-Version mit >= VERSION_MIN_TRADES Trades war positiv."""
    st = stats or {}
    n = int(st.get("trades") or 0)
    if n < CHRONIC_MIN_TRADES:
        return None
    pnl = float(st.get("pnl") or 0)
    wr = float(st.get("winrate") if st.get("winrate") is not None
               else (int(st.get("wins") or 0) / n * 100))
    if pnl >= 0 or wr >= CHRONIC_MAX_WINRATE:
        return None
    for v in versions or []:
        vs = (v or {}).get("stats") or {}
        if int(vs.get("trades") or 0) >= VERSION_MIN_TRADES and float(vs.get("pnl") or 0) > 0:
            return None
    return (f"chronisch negativ: {n} Trades in {LOOKBACK_DAYS} Tagen, Winrate {wr:.0f} %, "
            f"PnL {pnl:+.2f} USDT – keine Parameter-Version war positiv")


def inactive_old_enough(entry: Optional[Dict], now: Optional[datetime] = None) -> bool:
    at = _parse((entry or {}).get("at"))
    if at is None:
        return False
    return ((now or _now()) - at) >= timedelta(days=INACTIVE_MIN_AGE_DAYS)


def is_due(state: Optional[Dict], now: Optional[datetime] = None) -> bool:
    last = _parse((state or {}).get("last_run"))
    return last is None or ((now or _now()) - last) >= timedelta(hours=RUN_EVERY_H)


def llm_budget_left(state: Optional[Dict], now: Optional[datetime] = None) -> int:
    now = now or _now()
    day = now.strftime("%Y-%m-%d")
    if (state or {}).get("llm_day") != day:
        return MAX_LLM_PER_DAY
    return max(0, MAX_LLM_PER_DAY - int((state or {}).get("llm_used") or 0))


def build_prompt(asset_class: str, sid: str, desc: str, kind: str, reason: str,
                 stats: Optional[Dict], diag: List[str], lessons: List[str],
                 prev_desc: Optional[str]) -> str:
    st = stats or {}
    n = int(st.get("trades") or 0)
    wr = st.get("winrate") if st.get("winrate") is not None else (round(int(st.get("wins") or 0) / n * 100) if n else 0)
    stat_line = (f"{n} Trades, Winrate {wr} %, PnL {float(st.get('pnl') or 0):+.2f} USDT"
                 if n else "keine Trades")
    parts = [f"SETUP '{sid}' in {ac.LABELS.get(asset_class, asset_class)} "
             f"({len(ac.symbols_of(asset_class))} Assets) – Status: {kind.upper()}: {reason}",
             f"Aktuelle Regel: {desc}",
             f"Ergebnis {LOOKBACK_DAYS} Tage: {stat_line}"]
    if diag:
        parts.append("Diagnose:\n" + "\n".join(f"- {d}" for d in diag[:6]))
    if lessons:
        parts.append("Backtest-Lektionen:\n" + "\n".join(f"- {x}" for x in lessons[:3]))
    if prev_desc:
        parts.append(f"Letzte Revision (hat NICHT gereicht): {prev_desc[:200]}")
    parts.append("Gib die überarbeitete Regel als JSON zurück.")
    return "\n\n".join(parts)


def _suspend_entry(reason: str, now: datetime) -> Dict:
    return {"at": now.isoformat(), "reason": reason, "suspended": True,
            "retest_at": (now + timedelta(days=SUSPEND_DAYS)).isoformat(),
            "paper_since": {"trades": 0}}


async def _notify(db, text: str) -> None:
    try:
        from core import state
        from services import notifications
        await notifications.telegram_notify(db, state.telegram, "setup_review", text,
                                            cooldown_min=60, dedupe_key=f"tg:setup_review:{text[:60]}")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Setup-Review Telegram übersprungen: {e}")


async def _revise_with_llm(db, cls: str, sid: str, kind: str, reason: str, scope: Dict,
                           stats: Optional[Dict]) -> Optional[Dict]:
    from services import ai_playbook, setup_diagnosis
    from services.ai_engine import ai_engine
    if not getattr(ai_engine, "key", None):
        return None
    desc = ai_playbook.class_setups(cls).get(sid) or ai_playbook.all_setups().get(sid, "")
    try:
        diag = await setup_diagnosis.for_setup(db, cls, sid, days=LOOKBACK_DAYS)
    except Exception:  # noqa: BLE001
        diag = []
    lessons: List[str] = []
    try:
        bt = await db.settings.find_one({"_id": "setup_backtest_state"}) or {}
        lessons = list((((bt.get("classes") or {}).get(cls) or {}).get(sid) or {}).get("lessons") or [])
    except Exception:  # noqa: BLE001
        pass
    prev = ((scope.get("revisions") or {}).get(sid) or {}).get("desc")
    prompt = build_prompt(cls, sid, desc, kind, reason, stats, diag, lessons, prev)
    try:
        text, _prov, model = await ai_engine.generate_for_role(ROLE, prompt, SYSTEM, temperature=0.3)
        data = ai_engine._parse_json(text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Setup-Review LLM {sid}@{cls} fehlgeschlagen: {e}")
        return None
    new_desc = str((data or {}).get("desc") or "").strip()
    if len(new_desc) < 20:
        return None
    res = await ai_playbook.revise_setup(db, cls, sid, new_desc,
                                         reason=f"Auto-Review ({kind}): {str(data.get('reason') or '')[:150]}",
                                         source="review", trade_target=data.get("trade_target"))
    return {**res, "model": model, "desc": new_desc}


async def run(db, now: Optional[datetime] = None) -> Dict:
    """Ein Review-Durchlauf über alle Klassen. Rückgabe: Zusammenfassung."""
    from services import ai_playbook
    now = now or _now()
    state = await db.settings.find_one({"_id": STATE_ID}) or {}
    if state.get("llm_day") != now.strftime("%Y-%m-%d"):
        state.update({"llm_day": now.strftime("%Y-%m-%d"), "llm_used": 0})
    doc = await db.settings.find_one({"_id": ai_playbook.STATE_ID}) or {}
    classes = dict(doc.get("classes") or {})
    library = ai_playbook.all_setups()
    suspended: List[str] = []
    revised: List[str] = []
    for cls in ac.CLASSES:
        scope = dict(classes.get(cls) or {})
        live_blocked = dict(scope.get("live_blocked") or {})
        stats = await ai_playbook.setup_stats(db, days=LOOKBACK_DAYS, asset_class=cls)
        lifecycle = scope.get("lifecycle") or {}
        candidates: List[Tuple[str, str, str]] = []
        changed = False
        for sid in library:
            if not ac.setup_allowed(cls, sid):
                continue
            versions = ((lifecycle.get(sid) or {}).get("versions") or [])
            why = chronic_reason(stats.get(sid), versions)
            if why:
                if not (live_blocked.get(sid) or {}).get("suspended"):
                    live_blocked[sid] = {**(live_blocked.get(sid) or {}), **_suspend_entry(why, now)}
                    changed = True
                    label = ac.LABELS[cls]
                    suspended.append(f"{sid}@{cls}")
                    await ai_playbook._feed(db, sid, (f"Setup '{sid}' in {label} DEAKTIVIERT (Live pausiert, "
                                                      f"Re-Test in {SUSPEND_DAYS} Tagen): {why}. Wird überarbeitet."),
                                            asset_class=cls, kind="suspended")
                    await _notify(db, f"⛔ *Setup deaktiviert* `{sid}` ({label})\n{why}\n"
                                      f"Live-Einstiege pausiert, KI überarbeitet das Setup.")
                candidates.append((sid, "chronisch negativ", why))
            elif sid in (scope.get("inactive") or {}) and inactive_old_enough(scope["inactive"].get(sid), now):
                candidates.append((sid, "inaktiv", str(scope["inactive"][sid].get("reason") or "")))
        if changed:
            scope["live_blocked"] = live_blocked
            classes[cls] = scope
            await db.settings.update_one({"_id": ai_playbook.STATE_ID},
                                         {"$set": {f"classes.{cls}.live_blocked": live_blocked}}, upsert=True)
            ai_playbook.invalidate_cache()
        for sid, kind, why in candidates:
            if llm_budget_left(state, now) <= 0:
                break
            ok, _ = ai_playbook.revision_allowed(cls, sid, scope, library, now)
            if not ok:
                continue
            state["llm_used"] = int(state.get("llm_used") or 0) + 1
            res = await _revise_with_llm(db, cls, sid, kind, why, scope, stats.get(sid))
            if res and res.get("status") == "ok":
                revised.append(f"{sid}@{cls}")
                await _notify(db, f"🛠 *Setup überarbeitet* `{sid}` ({ac.LABELS[cls]}, {kind}, "
                                  f"v{res.get('version')})\n{res.get('desc', '')[:250]}")
    state.update({"_id": STATE_ID, "last_run": now.isoformat(),
                  "last_result": {"suspended": suspended, "revised": revised}})
    await db.settings.replace_one({"_id": STATE_ID}, state, upsert=True)
    if suspended or revised:
        ai_playbook.invalidate_cache()
        logger.info(f"Setup-Review: deaktiviert {suspended or '-'}, überarbeitet {revised or '-'}")
    return {"suspended": suspended, "revised": revised, "llm_used": state.get("llm_used", 0)}


async def maybe_run(db) -> Optional[Dict]:
    """Aus dem Engine-Loop: höchstens alle RUN_EVERY_H Stunden."""
    state = await db.settings.find_one({"_id": STATE_ID}, {"last_run": 1})
    if not is_due(state):
        return None
    return await run(db)
