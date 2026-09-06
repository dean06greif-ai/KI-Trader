"""Runner des Backtest-Seedings: Job-Verwaltung, Kerzen laden, In-Sample/
Out-of-Sample-Split, Varianten-Schleife, Speicherung, Playbook-Refresh.

Ablauf je Anlageklasse:
  1. Symbole der Klasse -> 1m-Kerzen (candle_cache, vorhandene Feeds) -> Features.
  2. Zeitraum wird geteilt: erste IS_SHARE (70 %) = In-Sample (wählt die Variante),
     Rest = Out-of-Sample (bestätigt). Nur OOS-Trades werden gespeichert.
  3. Je Setup: aktuelle Variante testen. "single": ein Durchlauf, bei Misserfolg
     wird die nächste Variante für den nächsten Lauf vorgemerkt (Anpassung).
     "loop": Varianten nacheinander, bis eine besteht oder alle durch sind.
  4. Bestanden -> OOS-Trades in `setup_backtest_trades` (eigene Collection – die
     Auswertungen echter auto_trades bleiben unberührt) + Feed-Meldung.
  5. ai_playbook.refresh(): Reife-Gate berücksichtigt den gedeckelten Anteil.

Setups, die in der Klasse bereits mit ECHTEN Daten live-reif sind, werden nicht
angefasst (übersprungen).
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import aiohttp

from services import candle_cache
from services import setup_asset_class as ac
from services import setup_lifecycle as lifecycle
from services.setup_backtest import detectors, simulator, weights
from services.setup_backtest.detectors import Features

logger = logging.getLogger(__name__)

STATE_ID = "setup_backtest_state"
DEFAULT_DAYS = 90
IS_SHARE = 0.7
MIN_OOS_TRADES = 10          # x 0.5 Gewicht = MIN_TRADES_PROMOTE
MIN_IS_TRADES = 10
MAX_DAYS = 365

JOBS: Dict[str, Dict] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(ms: int) -> str:
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Reine Helfer (testbar)
# --------------------------------------------------------------------------
def stats_of(trades: List[Dict]) -> Dict:
    n = len(trades)
    wins = sum(1 for t in trades if float(t.get("realized_pnl") or 0) > 0)
    pnl = sum(float(t.get("realized_pnl") or 0) for t in trades)
    return {"trades": n, "wins": wins, "pnl": round(pnl, 2),
            "winrate": round(wins / n * 100) if n else 0,
            "margin": round(n * simulator.MARGIN, 2)}


def passed(is_stats: Dict, oos_stats: Dict) -> bool:
    """Edge in BEIDEN Fenstern: genug Trades, PnL > 0 nach Gebühren und das
    Reife-Kriterium (PnL > 0 oder WR >= 55 %) auch Out-of-Sample."""
    if int(is_stats.get("trades") or 0) < MIN_IS_TRADES \
            or int(oos_stats.get("trades") or 0) < MIN_OOS_TRADES:
        return False
    if float(is_stats.get("pnl") or 0) <= 0 or float(oos_stats.get("pnl") or 0) <= 0:
        return False
    return lifecycle.promotion_ok(oos_stats)[0]


def next_variant(idx: int, total: int) -> Dict:
    """Nach einem Fehlschlag: nächste Variante vormerken (Anpassung)."""
    if idx + 1 < total:
        return {"variant": idx + 1, "status": "failed"}
    return {"variant": 0, "status": "exhausted"}


def run_variant(setup: str, feats: Dict[str, Features], variant_idx: int, asset_class: str,
                split_ts: int, job_id: str) -> Dict:
    """Ein Setup, eine Variante über alle Symbole der Klasse (rein, CPU)."""
    fee = simulator.FEES_RT_PCT.get(asset_class, 0.06)
    variant = detectors.VARIANTS[setup][variant_idx]
    is_trades: List[Dict] = []
    oos_trades: List[Dict] = []
    signals = 0
    for sym, f in feats.items():
        busy_until = -1
        c = f.c5
        for sig in detectors.run_detector(setup, f, variant_idx):
            if sig.idx <= busy_until:
                continue
            sig = simulator.clamp_signal(asset_class, sig)
            if sig is None:
                continue
            signals += 1
            res = simulator.simulate(c, sig, fee)
            if res is None:
                continue
            busy_until = res["exit_idx"]
            opened_ms = int(c.ts[sig.idx]) + detectors.M5
            trade = {"id": str(uuid.uuid4()), "job_id": job_id, "strategy_id": "ai_trader",
                     "mode": "backtest", "backtest": True, "data_collection": True,
                     "asset_class": asset_class, "setup": setup, "symbol": sym,
                     "side": sig.side, "timeframe": "5m", "variant": variant["name"],
                     "opened_at": _iso(opened_ms),
                     "closed_at": _iso(int(c.ts[res["exit_idx"]]) + detectors.M5),
                     "entry": sig.entry, "initial_sl": res["initial_sl"], "sl": res["sl"],
                     "tp1": res["tp1"], "tpf": res["tpf"], "exit_price": res["exit_price"],
                     "peak_price": res["peak_price"], "trough_price": res["trough_price"],
                     "realized_pnl": res["pnl"], "margin_used": simulator.MARGIN,
                     "result": res["result"], "exit_reason": res["reason"],
                     "note": sig.note, "oos": opened_ms >= split_ts}
            (oos_trades if trade["oos"] else is_trades).append(trade)
    is_st, oos_st = stats_of(is_trades), stats_of(oos_trades)
    return {"setup": setup, "variant": variant_idx, "variant_name": variant["name"],
            "variants_total": len(detectors.VARIANTS[setup]), "signals": signals,
            "is": is_st, "oos": oos_st, "passed": passed(is_st, oos_st),
            "oos_trades": oos_trades}


# --------------------------------------------------------------------------
# State (settings.setup_backtest_state)
# --------------------------------------------------------------------------
async def load_state(db) -> Dict:
    return await db.settings.find_one({"_id": STATE_ID}) or {"_id": STATE_ID, "classes": {}}


async def save_state(db, state: Dict) -> None:
    doc = {k: v for k, v in state.items() if k != "_id"}
    await db.settings.update_one({"_id": STATE_ID}, {"$set": doc}, upsert=True)


async def reset(db, asset_class: Optional[str] = None, setup: Optional[str] = None) -> int:
    match: Dict = {}
    if asset_class:
        match["asset_class"] = asset_class
    if setup:
        match["setup"] = setup
    res = await db[weights.COLLECTION].delete_many(match)
    state = await load_state(db)
    classes = dict(state.get("classes") or {})
    if asset_class and setup:
        (classes.get(asset_class) or {}).pop(setup, None)
    elif asset_class:
        classes.pop(asset_class, None)
    else:
        classes = {}
    state["classes"] = classes
    await save_state(db, state)
    return int(res.deleted_count)


def eligible_setups(asset_class: str, library: Dict[str, str], only: Optional[List[str]] = None) -> List[str]:
    return [s for s in detectors.VARIANTS
            if s in library and ac.setup_allowed(asset_class, s) and (not only or s in only)]


def no_edge_line(label: str, cls_state: Optional[Dict]) -> Optional[str]:
    """Prompt-Zeile (rein): Setups, deren regelbasierte Varianten in dieser
    Klasse OHNE Edge blieben – Hinweis für die KI, kein Ausschluss."""
    bad = sorted(sid for sid, e in (cls_state or {}).items()
                 if isinstance(e, dict) and e.get("status") == "exhausted")
    if not bad:
        return None
    return (f"BACKTEST OHNE EDGE {label} (alle regelbasierten 5m-Varianten IS/OOS negativ, "
            f"nur Hinweis – dort besonders selektiv/klein antesten): {', '.join(bad)}")


# --------------------------------------------------------------------------
# Job
# --------------------------------------------------------------------------
def create_job(params: Dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "kind": "ai_seed", "status": "running", "progress": 0,
                    "phase": "Startet", "params": params, "cancel": False,
                    "created_at": _now_iso(), "result": None, "error": None}
    for k in list(JOBS.keys())[:-5]:
        JOBS.pop(k, None)
    return job_id


def running_job() -> Optional[Dict]:
    return next((j for j in JOBS.values() if j["status"] == "running"), None)


async def _feed(db, text: str, **extra) -> None:
    try:
        await db.ai_chat.insert_one({"id": str(uuid.uuid4()), "role": "playbook", "setup": None,
                                     "text": text, "ts": _now_iso(), "backtest": True, **extra})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Backtest-Seeding Feed fehlgeschlagen: {e}")


async def _load_features(session, symbols: List[str], days: int, job: Dict,
                         asset_class: Optional[str] = None,
                         pct_from: int = 0, pct_to: int = 0) -> Dict[str, Features]:
    feats: Dict[str, Features] = {}
    for k, sym in enumerate(symbols):
        if job.get("cancel"):
            raise asyncio.CancelledError()
        job["phase"] = f"Lade Daten: {sym}"
        job["progress"] = int(pct_from + (pct_to - pct_from) * k / max(1, len(symbols)))
        try:
            ca1 = await candle_cache.get_candles(session, sym, days, job=job)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Backtest-Seeding: Historie {sym} nicht ladbar: {e}")
            continue
        if len(ca1) < 2000:
            logger.info(f"Backtest-Seeding: {sym} zu wenig Kerzen ({len(ca1)})")
            continue
        # Historie auf Platte sichern: der nächste Durchlauf (auch nach Neustart)
        # startet dann in Sekunden statt Minuten
        try:
            await candle_cache.persist_symbol_async(sym)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Backtest-Seeding: persist {sym}: {e}")
        feats[sym] = await asyncio.to_thread(Features, ca1, asset_class)
    return feats


async def run_job(job_id: str, db, asset_classes: List[str], days: int = DEFAULT_DAYS,
                  mode: str = "single", setups: Optional[List[str]] = None) -> None:
    from services import ai_playbook
    job = JOBS[job_id]
    days = min(max(int(days or DEFAULT_DAYS), 14), MAX_DAYS)
    mode = "loop" if mode == "loop" else "single"
    rows: List[Dict] = []
    try:
        pb = await ai_playbook.refresh(db)
        library = ai_playbook.all_setups()
        state = await load_state(db)
        classes_state = dict(state.get("classes") or {})
        plan = []
        for cls in asset_classes:
            if cls in ac.CLASSES:
                plan.append((cls, eligible_setups(cls, library, setups)))
        total_steps = sum(len(s) + 2 for _, s in plan) or 1
        step = 0
        async with aiohttp.ClientSession() as session:
            for cls, sids in plan:
                label = ac.LABELS[cls]
                cls_state = dict(classes_state.get(cls) or {})
                cls_pb = (pb.get("classes") or {}).get(cls) or {}
                symbols = ac.symbols_of(cls)
                pct_from = int(step / total_steps * 100)
                # Datenladen zählt wie 2 Setups (Download dominiert die Laufzeit)
                feats = await _load_features(session, symbols, days, job, cls,
                                             pct_from, int((step + 2) / total_steps * 100))
                step += 2
                job["progress"] = int(step / total_steps * 100)
                if not feats:
                    rows.append({"asset_class": cls, "setup": None, "status": "no_data",
                                 "note": f"{label}: keine Historie ladbar"})
                    continue
                first = min(int(f.c5.ts[0]) for f in feats.values())
                last = max(int(f.c5.ts[-1]) for f in feats.values())
                split_ts = int(first + (last - first) * IS_SHARE)
                for sid in sids:
                    if job.get("cancel"):
                        raise asyncio.CancelledError()
                    step += 1
                    job["progress"] = int(step / total_steps * 100)
                    job["phase"] = f"{label}: {sid}"
                    ready_real = bool((cls_pb.get("live_ready") or {}).get(sid)) \
                        and sid not in (cls_pb.get("bt_promoted") or {})
                    if ready_real:
                        rows.append({"asset_class": cls, "setup": sid, "status": "live",
                                     "note": "bereits mit echten Daten live-reif – nicht angefasst"})
                        continue
                    entry = dict(cls_state.get(sid) or {})
                    vi = int(entry.get("variant") or 0) % len(detectors.VARIANTS[sid])
                    history = list(entry.get("history") or [])[-9:]
                    tried = 0
                    res = None
                    while True:
                        res = await asyncio.to_thread(run_variant, sid, feats, vi, cls, split_ts, job_id)
                        tried += 1
                        history.append({"variant": vi, "name": res["variant_name"], "is": res["is"],
                                        "oos": res["oos"], "passed": res["passed"], "at": _now_iso()})
                        if res["passed"] or mode == "single" or tried >= res["variants_total"]:
                            break
                        vi = (vi + 1) % res["variants_total"]
                    if res["passed"]:
                        entry.update({"variant": vi, "status": "passed"})
                    else:
                        nxt = next_variant(vi, res["variants_total"])
                        if mode == "loop":
                            nxt["status"] = "exhausted"
                        entry.update(nxt)
                    entry.update({"name": res["variant_name"], "is": res["is"], "oos": res["oos"],
                                  "history": history[-10:], "updated_at": _now_iso(), "days": days,
                                  "symbols": sorted(feats), "signals": res["signals"],
                                  "split_at": _iso(split_ts)})
                    cls_state[sid] = entry
                    await db[weights.COLLECTION].delete_many({"asset_class": cls, "setup": sid})
                    if res["passed"] and res["oos_trades"]:
                        now = _now_iso()
                        await db[weights.COLLECTION].insert_many(
                            [{**t, "run_at": now} for t in res["oos_trades"]])
                    rows.append({"asset_class": cls, "setup": sid, "status": entry["status"],
                                 "variant": res["variant_name"], "variant_idx": vi,
                                 "variants_total": res["variants_total"], "tried": tried,
                                 "is": res["is"], "oos": res["oos"], "signals": res["signals"],
                                 "stored": len(res["oos_trades"]) if res["passed"] else 0,
                                 "symbols": sorted(feats)})
                classes_state[cls] = cls_state
                n_pass = sum(1 for r in rows if r.get("asset_class") == cls and r.get("status") == "passed")
                n_test = sum(1 for r in rows if r.get("asset_class") == cls and r.get("is"))
                if n_test:
                    await _feed(db, (f"Backtest-Seeding {label} ({days} Tage, {mode}): {n_test} Setups getestet, "
                                     f"{n_pass} mit Edge in In- und Out-of-Sample. Backtest-Trades zählen ×"
                                     f"{weights.BACKTEST_WEIGHT:g} und gedeckelt fürs Reife-Gate – Live erst nach "
                                     f"{weights.MIN_REAL_TRADES}+ profitablen echten Paper-Trades."),
                                asset_class=cls)
        state["classes"] = classes_state
        summary = {"kind": "ai_seed", "mode": mode, "days": days, "asset_classes": asset_classes,
                   "rows": rows, "finished_at": _now_iso(),
                   "passed": sum(1 for r in rows if r.get("status") == "passed"),
                   "tested": sum(1 for r in rows if r.get("is"))}
        state["last_result"] = summary
        await save_state(db, state)
        ai_playbook.invalidate_cache()
        await ai_playbook.refresh(db)
        job.update({"status": "done", "progress": 100, "phase": "Fertig", "result": summary})
    except asyncio.CancelledError:
        job.update({"status": "cancelled", "phase": "Abgebrochen",
                    "result": {"kind": "ai_seed", "rows": rows}})
    except Exception as e:  # noqa: BLE001
        logger.exception(f"Backtest-Seeding {job_id} fehlgeschlagen: {e}")
        job.update({"status": "error", "error": str(e)[:300], "phase": "Fehler"})


async def overview(db) -> Dict:
    """Für UI/API: Zustand je Klasse × Setup, testbare Setups, Regeln."""
    from services import ai_playbook
    state = await load_state(db)
    library = ai_playbook.all_setups()
    return {"classes": state.get("classes") or {},
            "last_result": state.get("last_result"),
            "eligible": {cls: eligible_setups(cls, library) for cls in ac.CLASSES},
            "not_backtestable": detectors.NOT_BACKTESTABLE,
            "variants": {sid: [v["name"] for v in vs] for sid, vs in detectors.VARIANTS.items()},
            "rules": {"weight": weights.BACKTEST_WEIGHT, "ttl_days": weights.BACKTEST_TTL_DAYS,
                      "min_real_trades": weights.MIN_REAL_TRADES,
                      "max_backtest_weighted": weights.MAX_BACKTEST_WEIGHTED,
                      "min_oos_trades": MIN_OOS_TRADES, "min_is_trades": MIN_IS_TRADES,
                      "is_share": IS_SHARE, "default_days": DEFAULT_DAYS, "max_days": MAX_DAYS},
            "running": running_job() is not None}
