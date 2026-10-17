"""Wiederherstellung verlorener Daten (Vorfall 05./06.09.2026).

Befund (audit_log + settings.history): Ein automatisierter Test-Lauf lief gegen
die PRODUKTIV-Datenbank und hat dabei
  * den MasterPrompt mit Test-Text ("UI-TEXT-42", "COMBO-POLICY-42") überschrieben,
  * Test-Lektionen ("TEST_…") angelegt,
  * über `analytics/clear` und `ai/trader/reset` Paper-Trades gelöscht.

Was hier wiederhergestellt wird – bewusst konservativ und idempotent:
  1. MasterPrompt: der letzte NICHT-Test-Stand aus der Versions-History.
  2. Lektionen: Test-Lektionen entfernen (nur eindeutige Test-Marker).
  3. Paper-Trades: aus `ai_trade_actions` (close/partial_close) rekonstruiert –
     PnL/Ergebnis/Zeiten sind dort exakt protokolliert; Entry/SL/TP kommen, wo
     vorhanden, aus dem passenden Signal. Rekonstruierte Trades tragen
     `recovered: True` (im UI erkennbar, für Reward-Backfill ausgeschlossen).

Trades, die per SL/TP-Treffer ohne KI-Aktion geschlossen wurden, hinterlassen
keine Spur mehr und sind NICHT rekonstruierbar – der Report nennt die Zahl.
Reine Prüf-/Bau-Funktionen sind modul-global (ohne DB) und testbar.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

MIN_REAL_TEXT_LEN = 200
MIN_REAL_POLICY_LEN = 100
_TEST_MARK_RE = re.compile(r"UI-TEXT-\d+|COMBO-POLICY|TESTPOLICY|\[QA_TE|QA_TEST|TEST_Lektion", re.I)
_TEST_LESSON_RE = re.compile(r"^(TEST_|QA_TEST|\[QA)|\bQA_TEST\b|TEST_Lektion", re.I)
SIGNAL_MATCH_WINDOW_MIN = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- MasterPrompt
def is_test_artifact(text: str, lesson_policy: str) -> bool:
    """Erkennt Test-Überbleibsel (rein, testbar): zu kurz oder mit Test-Marker."""
    t, p = str(text or ""), str(lesson_policy or "")
    if _TEST_MARK_RE.search(t) or _TEST_MARK_RE.search(p):
        return True
    return len(t.strip()) < MIN_REAL_TEXT_LEN or len(p.strip()) < MIN_REAL_POLICY_LEN


def pick_restorable_master(history: List[Dict]) -> Optional[Dict]:
    """Neuester History-Eintrag, der kein Test-Artefakt ist."""
    for h in sorted(history or [], key=lambda x: int(x.get("version") or 0), reverse=True):
        if not is_test_artifact(h.get("text"), h.get("lesson_policy")):
            return h
    return None


# ---------------------------------------------------------------- Lektionen
def is_test_lesson(lesson: Dict) -> bool:
    return bool(_TEST_LESSON_RE.search(str((lesson or {}).get("title") or "")))


# ---------------------------------------------------------------- Paper-Trades
def trade_open_ms(trade_id: str) -> Optional[int]:
    """Trade-IDs haben das Format SYMBOL-<epoch_ms>."""
    try:
        return int(str(trade_id).rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


MIN_MANUAL_DURATION_S = 60


def is_test_like_duration(opened_at: str, closed_at: str) -> bool:
    """Manuell per API geöffnet und binnen Sekunden wieder geschlossen = Test-Lauf."""
    try:
        a = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    return 0 <= (b - a).total_seconds() < MIN_MANUAL_DURATION_S


def rebuild_trade(trade_id: str, actions: List[Dict], signal: Optional[Dict] = None) -> Optional[Dict]:
    """Geschlossenen Paper-Trade aus seinen Aktionen rekonstruieren (rein, testbar).

    Ohne `close`-Aktion ist kein Endzustand bekannt -> None."""
    acts = sorted([a for a in actions if isinstance(a, dict)], key=lambda a: str(a.get("ts") or ""))
    close = next((a for a in reversed(acts) if a.get("action") == "close"), None)
    if not close:
        return None
    first = acts[0]
    open_ms = trade_open_ms(trade_id)
    opened_at = (datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc).isoformat()
                 if open_ms else str(first.get("ts") or ""))
    pnl = 0.0
    for a in acts:
        if a.get("action") in ("close", "partial_close"):
            try:
                pnl += float(((a.get("result") or {}).get("realized_pnl")) or 0)
            except (TypeError, ValueError):
                continue
    res = (close.get("result") or {}).get("result")
    if res not in ("win", "loss", "breakeven"):
        res = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
    source = str(close.get("source") or first.get("source") or "ki")
    manual = source in ("user", "manual", "manuell")
    if manual and is_test_like_duration(opened_at, str(close.get("ts") or "")):
        return None   # Sekunden-Trades per API = Test-Artefakt, nicht rekonstruieren
    sig = signal or {}
    trade = {
        "id": trade_id, "symbol": first.get("symbol"), "side": first.get("side"),
        "mode": first.get("mode") or "paper", "status": "closed", "result": res,
        "realized_pnl": round(pnl, 6), "opened_at": opened_at,
        "closed_at": str(close.get("ts") or ""), "trade_date": opened_at[:10],
        "strategy_id": "manual" if manual else "ai_trader",
        "strategy_name": "Manuell" if manual else "KI Trader",
        "manual_trade": manual, "close_reason": str(close.get("reason") or "")[:300],
        "entry": sig.get("entry_price"), "sl": sig.get("stop_loss"),
        "tp1": sig.get("take_profit_1"), "tpf": sig.get("take_profit_full"),
        "signal_id": sig.get("id"), "decision_id": sig.get("decision_id"),
        "ai_confidence": sig.get("ai_confidence"), "setup": sig.get("ai_setup"),
        "horizon": sig.get("ai_horizon"), "ai_reasoning": sig.get("ai_reasoning"),
        "data_collection": bool(sig.get("data_collection")) if sig else False,
        "recovered": True, "recovered_from": "ai_trade_actions",
        "recovered_at": _now_iso(), "actions_count": len(acts),
    }
    return {k: v for k, v in trade.items() if v is not None}


async def _find_signal(db, symbol: str, open_ms: Optional[int]) -> Optional[Dict]:
    if not open_ms or not symbol:
        return None
    t0 = datetime.fromtimestamp(open_ms / 1000, tz=timezone.utc)
    win = timedelta(minutes=SIGNAL_MATCH_WINDOW_MIN)
    return await db.signals.find_one(
        {"symbol": symbol, "strategy_id": "ai_trader",
         "timestamp": {"$gte": (t0 - win).isoformat(), "$lte": (t0 + win).isoformat()}},
        {"_id": 0})


async def _recover_trades(db, dry_run: bool) -> Dict:
    existing = {t["id"] for t in await db.auto_trades.find({}, {"id": 1}).to_list(100000)}
    by_trade: Dict[str, List[Dict]] = {}
    async for a in db.ai_trade_actions.find({"mode": "paper"}, {"_id": 0}):
        tid = a.get("trade_id")
        if tid and tid not in existing:
            by_trade.setdefault(tid, []).append(a)
    recovered: List[Dict] = []
    unrecoverable = 0
    with_levels = 0
    skipped_test_like = 0
    for tid, acts in by_trade.items():
        sig = await _find_signal(db, acts[0].get("symbol"), trade_open_ms(tid))
        trade = rebuild_trade(tid, acts, sig)
        if trade is None:
            if any(a.get("action") == "close" for a in acts):
                skipped_test_like += 1
            else:
                unrecoverable += 1
            continue
        with_levels += 1 if trade.get("entry") else 0
        recovered.append(trade)
    if recovered and not dry_run:
        await db.auto_trades.insert_many([dict(t) for t in recovered])
    pnl = round(sum(float(t.get("realized_pnl") or 0) for t in recovered), 2)
    return {"missing_paper_trades": len(by_trade), "recovered": len(recovered),
            "with_levels": with_levels, "unrecoverable_no_close": unrecoverable,
            "skipped_test_like": skipped_test_like, "recovered_pnl": pnl,
            "sample": [{k: t.get(k) for k in ("id", "side", "result", "realized_pnl", "opened_at")}
                       for t in recovered[:5]]}


async def _recover_master(db, dry_run: bool) -> Dict:
    from services.ai_master_prompt import DOC_ID, master_prompt
    doc = await db.settings.find_one({"_id": DOC_ID}) or {}
    if not is_test_artifact(doc.get("text"), doc.get("lesson_policy")):
        return {"restored": False, "reason": "aktueller Stand ist kein Test-Artefakt"}
    src = pick_restorable_master(doc.get("history") or [])
    if not src:
        return {"restored": False, "reason": "kein brauchbarer History-Stand"}
    out = {"restored": True, "from_version": src.get("version"),
           "rules": src.get("rules"), "text_head": str(src.get("text") or "")[:80]}
    if not dry_run:
        await master_prompt.save(text=src.get("text"), rules=src.get("rules"),
                                 lesson_policy=src.get("lesson_policy"),
                                 editor="recovery")
    return out


async def _clean_lessons(db, dry_run: bool) -> Dict:
    from services.ai_lessons import lesson_store
    lessons = await lesson_store.all()
    bad = [l for l in lessons if is_test_lesson(l)]
    if bad and not dry_run:
        await lesson_store.save_all([l for l in lessons if not is_test_lesson(l)])
    return {"removed": [l.get("title") for l in bad], "kept": len(lessons) - len(bad)}


async def run(db, dry_run: bool = False) -> Dict:
    """Alle Schritte ausführen (dry_run=True: nur Report, keine Schreibzugriffe)."""
    report = {"dry_run": dry_run, "at": _now_iso()}
    for key, fn in (("master_prompt", _recover_master), ("lessons", _clean_lessons),
                    ("paper_trades", _recover_trades)):
        try:
            report[key] = await fn(db, dry_run)
        except Exception as e:  # noqa: BLE001
            logger.exception(f"Recovery-Schritt {key} fehlgeschlagen: {e}")
            report[key] = {"error": str(e)[:300]}
    if not dry_run:
        await db.settings.update_one({"_id": "data_recovery"},
                                     {"$set": {"last_report": report}}, upsert=True)
        logger.info(f"Data-Recovery: {report}")
    return report
