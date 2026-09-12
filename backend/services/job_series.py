"""Nacht-Serie / Job-Warteschlange: Backtests, Optimierungen und Regime-Lab-
Analysen als Serie einplanen, die nacheinander automatisch durchläuft.

Architektur (bewusst dünn – nutzt die bestehenden Job-Pfade 1:1):
  * Ein Serien-Eintrag speichert nur `kind` + den Request-Body, den die UI
    auch beim direkten Start schicken würde (`/api/backtest/run`,
    `/api/optimizer/run`, `/api/regime-lab/analyze`).
  * Der Worker (run_loop) startet den nächsten Eintrag über GENAU diese
    Router-Funktionen (gleiche Validierung, Cloud/Lokal, RAM-Warteschlange),
    wartet bis der Job fertig ist und speichert eine kompakte Zusammenfassung
    + die job_id. Das vollständige Ergebnis liegt wie gewohnt in den
    bestehenden Collections (backtests, optimizer_runs, Regime-Analysen) und
    wird in der UI über die bestehenden Endpoints geladen.
  * Persistenz in Mongo (`job_series`), Serien-Einstellungen in
    settings['job_series_state'] (paused, start_at, notify_each, notify_done).
  * Es läuft immer nur EIN Rechenjob (die Router lehnen parallele Starts mit
    409 ab) – der Worker wartet, solange irgendwo ein Job läuft.
  * Telegram: Meldung pro fertigem Eintrag (notify_each) und wenn die Serie
    komplett durch ist (notify_done) – Toggle-Typ 'job_series'.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

KINDS = ("backtest", "optimizer", "regime_analysis")
STATE_ID = "job_series_state"
POLL_SEC = 5
ITEM_TIMEOUT_H = 14
MAX_ITEMS = 60
DEFAULT_STATE = {"paused": False, "start_at": None, "notify_each": True,
                 "notify_done": True}

_worker: Optional[asyncio.Task] = None
_current: Optional[str] = None  # id des gerade laufenden Serien-Eintrags


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- rein & testbar
def default_label(kind: str, body: Dict) -> str:
    """Lesbarer Name für einen Eintrag aus dem Request-Body."""
    syms = body.get("symbols") or []
    sym_txt = ", ".join(syms[:3]) + (f" +{len(syms) - 3}" if len(syms) > 3 else "")
    days = body.get("days")
    if kind == "backtest":
        strats = body.get("strategy_ids") or []
        s_txt = ", ".join(strats[:2]) + (f" +{len(strats) - 2}" if len(strats) > 2 else "")
        return f"Backtest {s_txt} · {sym_txt} · {days}d"
    if kind == "optimizer":
        mode = body.get("mode") or "params"
        target = body.get("strategy_id") if mode in ("params", "dynamic") else mode
        return f"Optimizer {mode}: {target} · {sym_txt} · {days}d · {body.get('timeframe') or ''}".strip(" ·")
    return f"Regime-Analyse {body.get('name') or ''} · {sym_txt} · {body.get('timeframe') or ''} · {days}d".replace("  ", " ")


def summarize(kind: str, result: Optional[Dict]) -> Dict:
    """Kompakte, einheiten-klare Kennzahlen (USDT / %) für die Serien-Tabelle."""
    out: Dict = {}
    if not isinstance(result, dict):
        return out
    if kind == "backtest":
        rows = [r for r in (result.get("per_strategy") or []) if isinstance(r, dict)]
        if rows:
            out["pnl"] = round(sum(_f(r.get("pnl")) or 0 for r in rows), 4)
            out["trades"] = int(sum(int(r.get("trades") or 0) for r in rows))
            out["max_drawdown"] = round(max((_f(r.get("max_drawdown")) or 0) for r in rows), 4)
            best = max(rows, key=lambda r: _f(r.get("pnl")) or 0)
            out["best"] = best.get("strategy_name") or best.get("strategy_id")
            wins = sum(int(r.get("wins") or 0) for r in rows)
            losses = sum(int(r.get("losses") or 0) for r in rows)
            if wins + losses:
                out["win_rate"] = round(wins / (wins + losses) * 100, 1)
        out["strategies"] = len(rows)
        out["capital"] = (result.get("config") or {}).get("max_capital")
        return out
    if kind == "optimizer":
        top = result.get("top5") or []
        best = top[0] if top and isinstance(top[0], dict) else {}
        m = (best.get("metrics") or result.get("metrics")
             or (result.get("best") or {}).get("metrics") or {})
        for k in ("pnl", "trades", "win_rate", "max_drawdown", "max_drawdown_pct", "profit_factor"):
            if m.get(k) is not None:
                out[k] = m[k]
        out["mode"] = result.get("mode")
        out["strategy"] = (result.get("strategy_name")
                           or (result.get("definition") or {}).get("name")
                           or result.get("strategy_id"))
        out["has_definition"] = bool(result.get("definition"))
        out["has_params"] = bool((result.get("best") or {}).get("params")
                                 or (result.get("best") or {}).get("trade_params"))
        out["wf"] = best.get("wf")
        out["passed"] = best.get("passed")
        out["capital"] = result.get("max_capital")
        return out
    out["analysis_id"] = result.get("analysis_id")
    out["regimes"] = result.get("regimes") or result.get("n_regimes")
    return out


def next_item(items: List[Dict]) -> Optional[Dict]:
    """Nächster wartender Eintrag nach Position (rein)."""
    queued = [i for i in items if i.get("status") == "queued"]
    if not queued:
        return None
    return sorted(queued, key=lambda i: (int(i.get("position") or 0), str(i.get("created_at"))))[0]


def may_start(state: Dict, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """Darf die Serie jetzt laufen? (pausiert / geplante Startzeit)"""
    if state.get("paused"):
        return False, "pausiert"
    start_at = state.get("start_at")
    if start_at:
        try:
            ts = datetime.fromisoformat(str(start_at))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            return True, "ungültige Startzeit ignoriert"
        now = now or datetime.now(timezone.utc)
        if now < ts:
            return False, f"geplant ab {ts.isoformat()[:16]} UTC"
    return True, "bereit"


def series_summary_text(done_items: List[Dict]) -> str:
    """Telegram-Text nach Abschluss der Serie (rein)."""
    lines = [f"🌙 *Nacht-Serie fertig* – {len(done_items)} Job(s)"]
    for it in done_items[:12]:
        s = it.get("summary") or {}
        st = it.get("status")
        mark = "✅" if st == "done" else ("⛔" if st == "cancelled" else "❌")
        parts = []
        if s.get("pnl") is not None:
            parts.append(f"PnL {float(s['pnl']):+.2f} USDT")
        if s.get("win_rate") is not None:
            parts.append(f"WR {float(s['win_rate']):.0f}%")
        if s.get("max_drawdown") is not None:
            parts.append(f"DD {float(s['max_drawdown']):.2f} USDT")
        if s.get("trades") is not None:
            parts.append(f"{int(s['trades'])} Trades")
        if s.get("analysis_id"):
            parts.append("Analyse gespeichert")
        if it.get("error"):
            parts.append(str(it["error"])[:80])
        lines.append(f"{mark} {it.get('label')}" + (f" – {' · '.join(parts)}" if parts else ""))
    if len(done_items) > 12:
        lines.append(f"… {len(done_items) - 12} weitere")
    lines.append("Ergebnisse: Analyse-Tools → Nacht-Serie")
    return "\n".join(lines)


def item_done_text(item: Dict, remaining: int) -> str:
    s = item.get("summary") or {}
    parts = []
    if s.get("pnl") is not None:
        parts.append(f"PnL {float(s['pnl']):+.2f} USDT")
    if s.get("win_rate") is not None:
        parts.append(f"WR {float(s['win_rate']):.0f}%")
    if s.get("max_drawdown") is not None:
        parts.append(f"DD {float(s['max_drawdown']):.2f} USDT")
    if s.get("trades") is not None:
        parts.append(f"{int(s['trades'])} Trades")
    mark = "✅" if item.get("status") == "done" else "❌"
    txt = f"{mark} *Serie: Job fertig* – {item.get('label')}"
    if parts:
        txt += "\n" + " · ".join(parts)
    if item.get("error"):
        txt += f"\nFehler: {str(item['error'])[:120]}"
    txt += f"\nNoch {remaining} in der Warteschlange"
    return txt


# ---------------------------------------------------------------- Persistenz
async def get_state(db) -> Dict:
    doc = await db.settings.find_one({"_id": STATE_ID}) or {}
    doc.pop("_id", None)
    return {**DEFAULT_STATE, **doc}


async def update_state(db, patch: Dict) -> Dict:
    upd: Dict = {}
    if "paused" in patch:
        upd["paused"] = bool(patch["paused"])
    if "notify_each" in patch:
        upd["notify_each"] = bool(patch["notify_each"])
    if "notify_done" in patch:
        upd["notify_done"] = bool(patch["notify_done"])
    if "start_at" in patch:
        v = patch["start_at"]
        if v in (None, "", False):
            upd["start_at"] = None
        else:
            ts = datetime.fromisoformat(str(v))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            upd["start_at"] = ts.isoformat()
    if upd:
        upd["updated_at"] = _now_iso()
        await db.settings.update_one({"_id": STATE_ID}, {"$set": upd}, upsert=True)
    return await get_state(db)


def _clean(doc: Dict) -> Dict:
    return {k: v for k, v in doc.items() if k != "_id"}


async def list_items(db, limit: int = 200) -> List[Dict]:
    rows = await db.job_series.find().sort([("position", 1), ("created_at", 1)]).to_list(limit)
    return [_clean(r) for r in rows]


def validate_body(kind: str, body: Dict, registry=None, symbols_ok=None) -> Optional[str]:
    """Früh-Validierung beim Einreihen (rein & testbar): dieselben Grundregeln
    wie die Start-Endpoints, damit ein Tippfehler nicht erst nachts auffällt.
    Rückgabe: Fehlertext oder None."""
    if registry is None:
        from strategies.registry import registry as _reg
        registry = _reg
    if symbols_ok is None:
        from core.config import BACKTEST_SYMBOLS
        symbols_ok = set(BACKTEST_SYMBOLS)
    syms = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not syms:
        return "Mindestens 1 Coin erforderlich"
    if kind in ("backtest", "optimizer"):
        bad = [s for s in syms if s not in symbols_ok]
        if bad:
            return f"Unbekannte Coins: {', '.join(bad[:5])}"
    if kind == "backtest":
        ids = body.get("strategy_ids") or []
        if not ids:
            return "strategy_ids erforderlich"
        missing = [s for s in ids if registry.get(s) is None]
        if missing:
            return f"Unbekannte Strategien: {', '.join(missing[:5])}"
    elif kind == "optimizer":
        mode = body.get("mode") or "params"
        if mode not in ("params", "discovery", "combo", "dynamic", "explore"):
            return "mode muss params|discovery|combo|dynamic|explore sein"
        if mode in ("params", "dynamic") and registry.get(body.get("strategy_id") or "") is None:
            return "Gültige strategy_id erforderlich"
    return None


async def add_item(db, kind: str, body: Dict, label: Optional[str] = None) -> Dict:
    if kind not in KINDS:
        raise ValueError("kind muss backtest|optimizer|regime_analysis sein")
    if not isinstance(body, dict) or not body:
        raise ValueError("body (Request wie beim direkten Start) erforderlich")
    err = validate_body(kind, body)
    if err:
        raise ValueError(err)
    n_open = await db.job_series.count_documents({"status": {"$in": ["queued", "running"]}})
    if n_open >= MAX_ITEMS:
        raise ValueError(f"Warteschlange voll (max. {MAX_ITEMS} offene Einträge)")
    last = await db.job_series.find().sort("position", -1).limit(1).to_list(1)
    pos = int((last[0].get("position") if last else 0) or 0) + 1
    item = {"id": uuid.uuid4().hex[:12], "kind": kind, "body": body,
            "label": (label or "").strip() or default_label(kind, body),
            "status": "queued", "position": pos, "created_at": _now_iso(),
            "started_at": None, "finished_at": None, "job_id": None,
            "summary": None, "error": None}
    await db.job_series.insert_one(dict(item))
    return item


async def remove_item(db, item_id: str) -> bool:
    doc = await db.job_series.find_one({"id": item_id})
    if not doc:
        return False
    if doc.get("status") == "running":
        await cancel_running(doc)
    await db.job_series.delete_one({"id": item_id})
    return True


async def clear_finished(db) -> int:
    res = await db.job_series.delete_many({"status": {"$in": ["done", "error", "cancelled"]}})
    return int(getattr(res, "deleted_count", 0) or 0)


async def reorder(db, ids: List[str]) -> int:
    n = 0
    for pos, iid in enumerate(ids, start=1):
        r = await db.job_series.update_one({"id": iid, "status": "queued"},
                                           {"$set": {"position": pos}})
        n += int(getattr(r, "modified_count", 0) or 0)
    return n


# ---------------------------------------------------------------- Ausführung
def _jobs_for(kind: str) -> Dict:
    if kind == "backtest":
        from services import backtester as bt
        return bt.JOBS
    if kind == "optimizer":
        from services import optimizer as opt
        return opt.JOBS
    from services import regime_lab as lab
    return lab.JOBS


def any_job_running() -> bool:
    """Läuft irgendwo (Backtester/Optimizer/Regime-Lab) gerade ein Rechenjob?"""
    for kind in KINDS:
        try:
            if any(j.get("status") == "running" for j in _jobs_for(kind).values()):
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


async def _start_job(kind: str, body: Dict) -> str:
    """Job über den bestehenden Router-Endpoint starten (gleiche Validierung,
    Cloud/Lokal, RAM-Warteschlange)."""
    body = dict(body)
    if kind == "backtest":
        from routers.backtest import start_backtest
        res = await start_backtest(body, True)
    elif kind == "optimizer":
        from routers.optimizer import start_optimizer
        res = await start_optimizer(body, True)
    else:
        from routers.regime_lab import start_analysis
        res = await start_analysis(body, True)
    return str(res["job_id"])


async def _wait_job(kind: str, job_id: str, item_id: str, db) -> Dict:
    """Bis der Job fertig ist pollen; Fortschritt am Serien-Eintrag spiegeln."""
    jobs = _jobs_for(kind)
    deadline = asyncio.get_event_loop().time() + ITEM_TIMEOUT_H * 3600
    last_progress = None
    while True:
        job = jobs.get(job_id)
        if job is None:
            # Job aus dem RAM verschwunden (Neustart/Verdrängung): Ergebnis aus DB?
            return {"status": "error", "error": "Job nicht mehr im Speicher (Server-Neustart?)",
                    "result": await _result_from_db(db, kind, job_id)}
        if job.get("status") != "running":
            return {"status": job.get("status"), "error": job.get("error"),
                    "result": job.get("result")}
        p = int(job.get("progress") or 0)
        if p != last_progress:
            last_progress = p
            await db.job_series.update_one(
                {"id": item_id}, {"$set": {"progress": p, "phase": job.get("phase")}})
        if asyncio.get_event_loop().time() > deadline:
            job["cancel"] = True
            return {"status": "error", "error": f"Zeitlimit {ITEM_TIMEOUT_H}h überschritten",
                    "result": None}
        await asyncio.sleep(POLL_SEC)


async def _result_from_db(db, kind: str, job_id: str) -> Optional[Dict]:
    coll = {"backtest": "backtests", "optimizer": "optimizer_runs"}.get(kind)
    if not coll:
        return None
    doc = await db[coll].find_one({"id": job_id})
    return (doc or {}).get("result")


async def cancel_running(item: Dict) -> None:
    job = _jobs_for(item["kind"]).get(item.get("job_id") or "")
    if job is not None:
        job["cancel"] = True
        job["phase"] = "Wird abgebrochen (Serie)..."


async def _notify(db, telegram, text: str):
    try:
        from services.notifications import telegram_notify
        await telegram_notify(db, telegram, "job_series", text)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Serie: Telegram-Meldung fehlgeschlagen: {e}")


async def run_one(db, item: Dict, telegram=None) -> Dict:
    """Einen Serien-Eintrag komplett ausführen (start -> warten -> speichern)."""
    global _current
    _current = item["id"]
    await db.job_series.update_one(
        {"id": item["id"]}, {"$set": {"status": "running", "started_at": _now_iso(),
                                      "progress": 0, "phase": "Startet", "error": None}})
    try:
        job_id = await _start_job(item["kind"], item.get("body") or {})
    except Exception as e:  # noqa: BLE001
        detail = getattr(e, "detail", None) or str(e)
        upd = {"status": "error", "error": str(detail)[:300], "finished_at": _now_iso()}
        await db.job_series.update_one({"id": item["id"]}, {"$set": upd})
        _current = None
        return {**item, **upd}
    await db.job_series.update_one({"id": item["id"]}, {"$set": {"job_id": job_id}})
    outcome = await _wait_job(item["kind"], job_id, item["id"], db)
    status = outcome.get("status") or "error"
    if status not in ("done", "cancelled"):
        status = "error"
    upd = {"status": status, "job_id": job_id, "finished_at": _now_iso(), "progress": 100,
           "error": (str(outcome.get("error"))[:300] if outcome.get("error") else None),
           "summary": summarize(item["kind"], outcome.get("result"))}
    await db.job_series.update_one({"id": item["id"]}, {"$set": upd})
    _current = None
    return {**item, **upd}


async def tick(db, telegram=None) -> Optional[Dict]:
    """Ein Worker-Durchlauf: nächsten Eintrag starten, wenn erlaubt und frei.
    Rückgabe: ausgeführter Eintrag oder None."""
    state = await get_state(db)
    ok, _why = may_start(state)
    if not ok:
        return None
    if any_job_running():
        return None
    items = await list_items(db)
    nxt = next_item(items)
    if not nxt:
        return None
    done_item = await run_one(db, nxt, telegram)
    remaining = await db.job_series.count_documents({"status": "queued"})
    if state.get("notify_each", True):
        await _notify(db, telegram, item_done_text(done_item, remaining))
    if remaining == 0:
        # Serie komplett: alle seit dem letzten Abschluss fertigen Einträge melden
        last_done = state.get("last_series_done_at") or ""
        finished = [i for i in await list_items(db)
                    if i.get("status") in ("done", "error", "cancelled")
                    and str(i.get("finished_at") or "") > str(last_done)]
        await db.settings.update_one(
            {"_id": STATE_ID}, {"$set": {"last_series_done_at": _now_iso(), "start_at": None}},
            upsert=True)
        if state.get("notify_done", True) and finished:
            await _notify(db, telegram, series_summary_text(finished))
    return done_item


async def run_loop(db, telegram=None, interval: int = POLL_SEC):
    logger.info("Nacht-Serie/Job-Warteschlange gestartet")
    # Nach Neustart: 'running'-Einträge ohne lebenden Job auf Fehler setzen
    try:
        stale = await db.job_series.find({"status": "running"}).to_list(50)
        for it in stale:
            await db.job_series.update_one(
                {"id": it["id"]}, {"$set": {"status": "error", "finished_at": _now_iso(),
                                            "error": "Server-Neustart während des Laufs",
                                            "summary": summarize(
                                                it["kind"], await _result_from_db(
                                                    db, it["kind"], it.get("job_id") or ""))}})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Serie: Aufräumen nach Neustart fehlgeschlagen: {e}")
    await asyncio.sleep(20)
    while True:
        try:
            await tick(db, telegram)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Serie: Worker-Fehler: {e}")
        await asyncio.sleep(interval)


def current_item_id() -> Optional[str]:
    return _current
