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
from services.candles import CandleArray
from services.setup_backtest import analysis, detectors, edges, simulator, weights
from services.setup_backtest.detectors import Features

logger = logging.getLogger(__name__)

STATE_ID = "setup_backtest_state"
DEFAULT_DAYS = 90
IS_SHARE = 0.7
MIN_OOS_TRADES = 10          # x 0.5 Gewicht = MIN_TRADES_PROMOTE (Untergrenze)
MIN_IS_TRADES = 10
MAX_DAYS = 365
# Adaptive Mindest-Trade-Anzahl (Overfitting-Bremse, 09/2026): 10 Trades auf
# 365 Tagen sind kein Edge. Erwartete Signale je Symbol und 30 Tage (kalibriert
# an den Prod-Läufen 09/2026: realistisch ~2/Symbol/Monat, session_open öfter,
# Squeeze/HTF-Range seltener) x Anzahl Assets der Klasse x Zeitraum -> davon
# muss ein Anteil (MIN_TRADES_SHARE) tatsächlich zustande kommen; Untergrenze =
# alte Werte, Obergrenze MIN_TRADES_CAP. Wenige Assets (Indizes: 2) -> kleines
# Ziel, viele Assets (Krypto: 11) -> größeres. Die KI darf per
# `min_trades_factor` (0.7-1.5) je Setup nachjustieren (revise.sanitize klemmt).
SETUP_FREQ_30D: Dict[str, float] = {
    "breakout": 2.0, "squeeze_breakout": 1.5, "range_fade": 2.0, "mean_reversion": 2.5,
    "htf_range": 1.5, "session_open": 3.0, "trend_follow": 2.0, "trend_follow2": 2.0,
    "pullback": 2.0, "divergence": 2.0,
}
MIN_TRADES_SHARE = 0.4
MIN_TRADES_CAP = (60, 40)    # (IS, OOS)
# Walk-Forward: das Out-of-Sample-Fenster wird in WF_WINDOWS gleich lange Zeit-
# Fenster geteilt; mind. WF_MIN_POS davon müssen positiv sein (robust über Marktphasen)
WF_WINDOWS = 3
WF_MIN_POS = 2

JOBS: Dict[str, Dict] = {}
PASSED_STATES = ("passed", "tuned")


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
    out = {"trades": n, "wins": wins, "pnl": round(pnl, 2),
           "winrate": round(wins / n * 100) if n else 0,
           "margin": round(n * simulator.MARGIN, 2)}
    if any("oos_window" in t for t in trades):
        wins_ = [{"trades": 0, "pnl": 0.0} for _ in range(WF_WINDOWS)]
        for t in trades:
            w = wins_[min(WF_WINDOWS - 1, max(0, int(t.get("oos_window") or 0)))]
            w["trades"] += 1
            w["pnl"] = round(w["pnl"] + float(t.get("realized_pnl") or 0), 2)
        out["windows"] = wins_
        out["windows_pos"] = sum(1 for w in wins_ if w["trades"] and w["pnl"] > 0)
    return out


def oos_window(opened_ms: int, split_ts: int, end_ts: int) -> int:
    """Index des Walk-Forward-Fensters (0..WF_WINDOWS-1) eines OOS-Trades (rein)."""
    span = max(1, int(end_ts) - int(split_ts))
    return min(WF_WINDOWS - 1, max(0, int((int(opened_ms) - int(split_ts)) * WF_WINDOWS // span)))


def min_trades_for(setup: str, n_symbols: int, days: float, factor: float = 1.0) -> Dict:
    """Mindest-Trades IS/OOS für Setup x Klassen-Größe x Zeitraum (rein).
    Floors = MIN_IS_TRADES/MIN_OOS_TRADES, Deckel MIN_TRADES_CAP."""
    freq = float(SETUP_FREQ_30D.get(setup, 5))
    try:
        fac = max(0.7, min(1.5, float(factor or 1.0)))
    except (TypeError, ValueError):
        fac = 1.0
    expected = freq * max(1, int(n_symbols or 1)) * max(1.0, float(days or 1)) / 30.0
    is_t = int(round(expected * IS_SHARE * MIN_TRADES_SHARE * fac))
    oos_t = int(round(expected * (1 - IS_SHARE) * MIN_TRADES_SHARE * fac))
    return {"is": max(MIN_IS_TRADES, min(MIN_TRADES_CAP[0], is_t)),
            "oos": max(MIN_OOS_TRADES, min(MIN_TRADES_CAP[1], oos_t)),
            "expected": int(round(expected)), "factor": fac}


def passed(is_stats: Dict, oos_stats: Dict, req: Optional[Dict] = None) -> bool:
    """Edge in BEIDEN Fenstern: genug Trades (adaptiv, `req` aus min_trades_for),
    PnL > 0 nach Gebühren und das Reife-Kriterium (PnL > 0 oder WR >= 55 %)
    auch Out-of-Sample. Liegen Walk-Forward-Fenster vor, müssen zusätzlich
    >= WF_MIN_POS davon positiv sein."""
    min_is = int((req or {}).get("is") or MIN_IS_TRADES)
    min_oos = int((req or {}).get("oos") or MIN_OOS_TRADES)
    if int(is_stats.get("trades") or 0) < min_is \
            or int(oos_stats.get("trades") or 0) < min_oos:
        return False
    if float(is_stats.get("pnl") or 0) <= 0 or float(oos_stats.get("pnl") or 0) <= 0:
        return False
    if oos_stats.get("windows") and int(oos_stats.get("windows_pos") or 0) < WF_MIN_POS:
        return False
    return lifecycle.promotion_ok(oos_stats)[0]


def next_variant(idx: int, total: int) -> Dict:
    """Nach einem Fehlschlag: nächste Variante vormerken (Anpassung)."""
    if idx + 1 < total:
        return {"variant": idx + 1, "status": "failed"}
    return {"variant": 0, "status": "exhausted"}


def run_variant(setup: str, feats: Dict[str, Features], variant_idx: int, asset_class: str,
                split_ts: int, job_id: str, params: Optional[Dict] = None) -> Dict:
    """Ein Setup, eine Variante (oder freier Parameter-Satz `params`) über alle
    Symbole der Klasse (rein, CPU)."""
    fee = simulator.FEES_RT_PCT.get(asset_class, 0.06)
    variant = params or detectors.VARIANTS[setup][variant_idx]
    max_bars = int(variant.get("max_bars") or simulator.MAX_BARS)
    end_ts = max(int(f.c5.ts[-1]) for f in feats.values()) if feats else split_ts
    start_ts = min(int(f.c5.ts[0]) for f in feats.values()) if feats else split_ts
    req = min_trades_for(setup, len(feats), (end_ts - start_ts) / 86_400_000,
                         variant.get("min_trades_factor", 1.0))
    is_trades: List[Dict] = []
    oos_trades: List[Dict] = []
    signals = 0
    for sym, f in feats.items():
        busy_until = -1
        c = f.c5
        sigs = (detectors.run_detector_params(setup, f, params) if params
                else detectors.run_detector(setup, f, variant_idx))
        for sig in sigs:
            if sig.idx <= busy_until:
                continue
            sig = simulator.clamp_signal(asset_class, sig)
            if sig is None:
                continue
            signals += 1
            res = simulator.simulate(c, sig, fee, max_bars=max_bars)
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
            if trade["oos"]:
                trade["oos_window"] = oos_window(opened_ms, split_ts, end_ts)
            (oos_trades if trade["oos"] else is_trades).append(trade)
    is_st, oos_st = stats_of(is_trades), stats_of(oos_trades)
    return {"setup": setup, "variant": variant_idx, "variant_name": variant["name"],
            "variants_total": len(detectors.VARIANTS[setup]), "signals": signals,
            "is": is_st, "oos": oos_st, "passed": passed(is_st, oos_st, req), "min_trades": req,
            "oos_trades": oos_trades, "params": dict(params) if params else None,
            "effective_params": {k: v for k, v in variant.items() if k != "name"},
            "diag": {"is": analysis.diagnose(is_trades, fee), "oos": analysis.diagnose(oos_trades, fee)}}


def best_base_variant(history: List[Dict]) -> Optional[int]:
    """Variante mit dem besten In-Sample-PnL (bei genug Trades) als Tuning-Basis."""
    cands = [h for h in (history or []) if int((h.get("is") or {}).get("trades") or 0) >= MIN_IS_TRADES]
    if not cands:
        return None
    return int(max(cands, key=lambda h: float((h.get("is") or {}).get("pnl") or 0)).get("variant") or 0)


def tune(setup: str, feats: Dict[str, Features], base_idx: int, asset_class: str,
         split_ts: int, job_id: str) -> Dict:
    """Feintuning um die beste Variante (rein): alle Kandidaten testen, den
    besten Out-of-Sample-Bestandenen zurückgeben. Rückgabe {'best', 'tried'}."""
    base = detectors.VARIANTS[setup][base_idx % len(detectors.VARIANTS[setup])]
    best = None
    tried = 0
    for params in detectors.tune_candidates(setup, base):
        res = run_variant(setup, feats, base_idx, asset_class, split_ts, job_id, params=params)
        tried += 1
        if res["passed"] and (best is None or float(res["oos"]["pnl"]) > float(best["oos"]["pnl"])):
            best = res
    return {"best": best, "tried": tried}


# --------------------------------------------------------------------------
# State (settings.setup_backtest_state)
# --------------------------------------------------------------------------
async def load_state(db) -> Dict:
    return await db.settings.find_one({"_id": STATE_ID}) or {"_id": STATE_ID, "classes": {}}


async def save_state(db, state: Dict) -> None:
    # "auto" (Automatik-Einstellungen) wird von setup_backtest.auto separat gepflegt
    doc = {k: v for k, v in state.items() if k not in ("_id", "auto")}
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
    # Edge-Register bleibt als Sicherheitsnetz erhalten (Rollback möglich),
    # aktive Edges werden aber deaktiviert -> Neustart der Bewertung
    q: Dict = {"status": "active"}
    if asset_class:
        q["asset_class"] = asset_class
    if setup:
        q["setup"] = setup
    await db[edges.COLLECTION].update_many(
        q, {"$set": {"status": "retired", "retired_at": _now_iso(), "retired_reason": "Reset"}})
    return int(res.deleted_count)


def eligible_setups(asset_class: str, library: Dict[str, str], only: Optional[List[str]] = None) -> List[str]:
    return [s for s in detectors.VARIANTS
            if s in library and ac.setup_allowed(asset_class, s) and (not only or s in only)]


def no_edge_line(label: str, cls_state: Optional[Dict]) -> Optional[str]:
    """Prompt-Zeile (rein): Setups, deren regelbasierte Varianten in dieser
    Klasse OHNE Edge blieben – Hinweis für die KI, kein Ausschluss."""
    bad = sorted(sid for sid, e in (cls_state or {}).items()
                 if isinstance(e, dict) and e.get("status") in ("exhausted", "stale"))
    # (Feingetunte Setups – status "tuned" – gelten als Setups MIT Edge; "stale" =
    #  Edge mehrfach nicht bestätigt, pausiert, im Register reaktivierbar)
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


def history_note(sym: str, days: int, eff_days: int, ca1: CandleArray, source: str) -> Optional[str]:
    """Datenhinweis (rein, testbar): nennt die TATSÄCHLICH geladene Historie statt
    eines festen Quellen-Limits. Bitunix-Kontrakte liefern ab Listing – die
    Historie wächst täglich, das Kerzen-Archiv (Supabase) hält sie dauerhaft."""
    if not len(ca1):
        return None
    avail = (int(ca1.ts[-1]) - int(ca1.ts[0])) / 86_400_000
    if avail >= days - 1.5:
        return None
    if eff_days < days:
        return (f"{sym}: {avail:.0f} von {days} Tagen geladen – Quelle ({source}) liefert "
                f"max. {eff_days} Tage")
    why = ("Quelle liefert erst ab Kontrakt-Listing – Historie wächst täglich weiter"
           if source == "bitunix" else f"Quelle ({source}) liefert nicht mehr")
    return f"{sym}: {avail:.0f} von {days} Tagen geladen ({why})"


def market_open_only(sym: str, ca1: CandleArray) -> CandleArray:
    """Nicht-Krypto: Kerzen aus Wochenend-/Handelspausen entfernen (eingefrorene
    Kurse würden EMA/ATR/RSI verfälschen und Phantom-Signale erzeugen). Krypto
    bleibt unverändert (24/7). Rein, testbar."""
    from core import market_hours
    if not len(ca1):
        return ca1
    m = market_hours.open_mask(sym, ca1.ts)
    if bool(m.all()):
        return ca1
    return CandleArray(ca1.ts[m], ca1.op[m], ca1.hi[m], ca1.lo[m], ca1.cl[m], ca1.vol[m])


async def _load_features(session, symbols: List[str], days: int, job: Dict,
                         asset_class: Optional[str] = None,
                         pct_from: int = 0, pct_to: int = 0) -> Dict[str, Features]:
    from services import history_sources
    feats: Dict[str, Features] = {}
    data_notes: List[str] = job.setdefault("data_notes", [])
    for k, sym in enumerate(symbols):
        if job.get("cancel"):
            raise asyncio.CancelledError()
        job["phase"] = f"Lade Daten: {sym}"
        job["progress"] = int(pct_from + (pct_to - pct_from) * k / max(1, len(symbols)))
        eff_days = history_sources.days_cap(sym, days)
        try:
            ca1 = await candle_cache.get_candles(session, sym, days, job=job)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Backtest-Seeding: Historie {sym} nicht ladbar: {e}")
            continue
        note = history_note(sym, days, eff_days, ca1, history_sources.source_of(sym))
        if note:
            data_notes.append(note)
        n_raw = len(ca1)
        ca1 = market_open_only(sym, ca1)
        if len(ca1) < n_raw:
            logger.info(f"Backtest-Seeding: {sym} {n_raw - len(ca1)} Kerzen außerhalb der Handelszeit entfernt")
        if len(ca1) < 2000:
            logger.info(f"Backtest-Seeding: {sym} zu wenig Kerzen ({len(ca1)})")
            data_notes.append(f"{sym}: zu wenig Historie ({len(ca1)} Kerzen)")
            continue
        # Historie auf Platte sichern: der nächste Durchlauf (auch nach Neustart)
        # startet dann in Sekunden statt Minuten
        try:
            await candle_cache.persist_symbol_async(sym)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Backtest-Seeding: persist {sym}: {e}")
        feats[sym] = await asyncio.to_thread(Features, ca1, asset_class)
    return feats


MODES = ("single", "loop", "ai_loop", "optimize")


def normalize_mode(mode) -> str:
    return mode if mode in MODES else "single"


# --------------------------------------------------------------------------
# Setup-Optimierer (Modus "optimize"): bereits Edge-validierte Setups auf mehr
# Trades / höhere Winrate optimieren, ohne den PnL-Edge aufzugeben.
# --------------------------------------------------------------------------
def optimize_candidates(setup: str, base: Dict) -> List[Dict]:
    """Kandidaten (rein): Risiko-/Ziel-Feintuning (tune_candidates) plus
    vorsichtige Einzel-Variation aller Basis-Parameter (x0.8 / x1.2, über
    revise.sanitize in den erlaubten Bereich geklemmt). Max. 16 Kandidaten."""
    from services.setup_backtest import revise
    cands = list(detectors.tune_candidates(setup, base))
    ranges = revise.param_ranges(setup)
    for k, v in base.items():
        if k == "name" or k in detectors.TUNE_KEYS or k not in ranges:
            continue
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        for fac in (0.8, 1.2):
            cand = revise.sanitize(setup, {k: v * fac}, base, 0)
            if cand:
                cand["name"] = f"{base.get('name', 'v')}·{k}×{fac:g}"
                cands.append(cand)
    return cands[:16]


def optimize_better(base: Dict, cand: Dict) -> bool:
    """Verbesserung (rein): Kandidat besteht das Edge-Kriterium, verliert keinen
    Out-of-Sample-PnL und bringt mehr Trades ODER (gleich viele + höhere WR)."""
    if not cand.get("passed"):
        return False
    b, c = base.get("oos") or {}, cand.get("oos") or {}
    if float(c.get("pnl") or 0) < float(b.get("pnl") or 0):
        return False
    bt, ct = int(b.get("trades") or 0), int(c.get("trades") or 0)
    return ct > bt or (ct >= bt and int(c.get("winrate") or 0) > int(b.get("winrate") or 0))


def optimize_score(res: Dict) -> tuple:
    o = res.get("oos") or {}
    return (int(o.get("trades") or 0), float(o.get("pnl") or 0), int(o.get("winrate") or 0))


async def _optimize_setup(job: Dict, db, cls: str, sid: str, entry: Dict, feats: Dict,
                          split_ts: int) -> Dict:
    """Ein Edge-validiertes Setup optimieren. Verschlechtert NIE den Zustand:
    ohne bessere Kandidaten bleibt der bisherige Parameter-Satz/Status stehen."""
    label = ac.LABELS[cls]
    job_id = job["id"]
    n_var = len(detectors.VARIANTS[sid])
    vi = int(entry.get("variant") or 0) % n_var
    active = await edges.get_active(db, cls, sid)
    base_params = ({**active["params"], "name": active.get("name") or "Edge"} if active
                   else effective_params_of(sid, entry) or dict(detectors.VARIANTS[sid][vi]))
    history = list(entry.get("history") or [])[-9:]
    job["phase"] = f"{label}: {sid} · Optimierer-Basis"
    base_res = await asyncio.to_thread(run_variant, sid, feats, vi, cls, split_ts,
                                       job_id, dict(base_params))
    history.append(_hist(base_res, vi, optimize=True, base_run=True))
    tried = 1
    best = None
    days = int(job["params"].get("days") or DEFAULT_DAYS)
    for params in optimize_candidates(sid, base_params):
        if job.get("cancel"):
            raise asyncio.CancelledError()
        job["phase"] = f"{label}: {sid} · Optimierer {params.get('name', '')}"
        res = await asyncio.to_thread(run_variant, sid, feats, vi, cls, split_ts,
                                      job_id, params)
        tried += 1
        # Overfitting-Bremse: Kandidaten mit harten Signalen (OOS-Abfall, PF,
        # Winrate deckt CRV nicht) werden nie übernommen
        if optimize_better(base_res, res) and not edges.overfit_flags(res)["hard"] \
                and (best is None or optimize_score(res) > optimize_score(best)):
            best = res
    improved = best is not None
    res = best or base_res
    if improved:
        history.append(_hist(best, vi, optimize=True, improved=True))
        entry["tuned"] = dict(best["params"] or {})
        entry["status"] = "tuned"
        entry.update({"name": best["variant_name"], "is": best["is"], "oos": best["oos"],
                      "min_trades": best.get("min_trades"), "stale": 0})
        entry.pop("ai_proposal", None)
        entry.pop("ai_proposal_meta", None)
        doc = await edges.record(db, cls, sid, edges.summarize(best, "optimize", vi), days=days,
                                 job_id=job_id, oos_trades=best["oos_trades"])
        await edges.set_active(db, cls, sid, doc["id"], reason="Optimierer: mehr Trades/Winrate bei >= OOS-PnL")
        if base_res["passed"]:
            await edges.record(db, cls, sid, edges.summarize(base_res, "check", vi), days=days, job_id=job_id)
        await edges.prune(db, cls, sid)
        entry.update({"edge_id": doc["id"], "robust": doc.get("robust"), "flags": doc.get("flags"),
                      "confirmations": doc.get("confirmations"), "edge_action": "replace",
                      "edge_note": "Optimierer: robusterer Satz übernommen"})
    elif base_res["passed"]:
        doc = await edges.record(db, cls, sid, edges.summarize(base_res, "check", vi), days=days,
                                 job_id=job_id, oos_trades=base_res["oos_trades"],
                                 status="active" if not active else "candidate")
        if not active:
            await edges.set_active(db, cls, sid, doc["id"], reason="Optimierer-Basis bestätigt")
        entry.update({"edge_id": doc["id"], "robust": doc.get("robust"), "flags": doc.get("flags"),
                      "confirmations": doc.get("confirmations"), "edge_action": "confirm",
                      "edge_note": "Optimierer: aktiver Edge bestätigt, kein besserer Satz", "stale": 0})
    # Ohne Verbesserung: Status/Parameter unangetastet lassen (kein Downgrade),
    # nur Verlauf/Zeitstempel aktualisieren.
    entry.update({"history": history[-10:], "updated_at": _now_iso(),
                  "symbols": sorted(feats), "split_at": _iso(split_ts),
                  "last_optimize": {"at": _now_iso(), "improved": improved,
                                    "tried": tried,
                                    "base_oos": base_res.get("oos"),
                                    "best_oos": (best or {}).get("oos")}})
    return {"res": res, "entry": entry, "tried": tried, "ai_rounds": 0,
            "ai_proposal": None, "improved": improved, "base": base_res}


def ai_options(opts: Optional[Dict]) -> Dict:
    """KI-Optionen (rein): ai_revise = Vorschlag nach Fehlschlag vormerken,
    ai_rounds = max. Revisions-Runden je Setup (ai_loop), target_passed =
    Ziel-Anzahl bestandener Setups je Klasse (ai_loop stoppt dann)."""
    from services.setup_backtest import revise
    opts = opts or {}
    try:
        rounds = max(1, min(revise.MAX_ROUNDS, int(opts.get("ai_rounds", revise.DEFAULT_ROUNDS))))
    except (TypeError, ValueError):
        rounds = revise.DEFAULT_ROUNDS
    try:
        target = max(1, min(20, int(opts.get("target_passed", revise.DEFAULT_TARGET))))
    except (TypeError, ValueError):
        target = revise.DEFAULT_TARGET
    return {"ai_revise": bool(opts.get("ai_revise", True)), "ai_rounds": rounds,
            "target_passed": target}


def _hist(res: Dict, vi: int, **extra) -> Dict:
    """Historien-Eintrag: neben IS/OOS-Summen auch die wirksamen Parameter und
    eine kompakte Diagnose – Grundlage der Lernschleife (analysis.lessons)."""
    return {"variant": vi, "name": res["variant_name"], "is": res["is"], "oos": res["oos"],
            "passed": res["passed"], "at": _now_iso(),
            "params": dict(res.get("effective_params") or {}),
            "diag": {"is": analysis.compact((res.get("diag") or {}).get("is")),
                     "oos": analysis.compact((res.get("diag") or {}).get("oos"))},
            **extra}


def rules_for_prompt(req: Optional[Dict] = None) -> Dict:
    return {"min_is_trades": int((req or {}).get("is") or MIN_IS_TRADES),
            "min_oos_trades": int((req or {}).get("oos") or MIN_OOS_TRADES),
            "expected_trades": (req or {}).get("expected"),
            "wf_windows": WF_WINDOWS, "wf_min_pos": WF_MIN_POS}


def effective_params_of(setup: str, entry: Optional[Dict]) -> Optional[Dict]:
    """Wirksamer Parameter-Satz eines Setups mit Edge (getunt/KI-Satz, sonst die
    bestandene Basis-Variante); None ohne Edge (rein)."""
    if not isinstance(entry, dict) or entry.get("status") not in PASSED_STATES:
        return None
    if isinstance(entry.get("tuned"), dict):
        return dict(entry["tuned"])
    variants = detectors.VARIANTS.get(setup) or []
    if not variants:
        return None
    return dict(variants[int(entry.get("variant") or 0) % len(variants)])


def active_params_of(sid: str, entry: Dict, active: Optional[Dict]) -> Optional[Dict]:
    """Parameter-Satz, der in diesem Lauf ZUERST geprüft wird (rein): der aktive
    Edge aus dem Register, sonst (Alt-Stand ohne Register) der bestandene Satz
    des State-Eintrags."""
    if active and isinstance(active.get("params"), dict):
        return {**active["params"], "name": active.get("name") or "Edge"}
    if isinstance(entry.get("tuned"), dict):
        return dict(entry["tuned"])
    if entry.get("status") in PASSED_STATES:
        p = effective_params_of(sid, entry)
        if p:
            return {**p, "name": entry.get("name") or "Edge"}
    return None


def _apply_decision(entry: Dict, decision: Dict, active: Optional[Dict], active_res: Optional[Dict],
                    cand: Optional[Dict], vi: int) -> Dict:
    """State-Eintrag nach der Edge-Entscheidung (rein). Rückgabe = `res`, das im
    Eintrag/in den Ergebniszeilen als Ergebnis dieses Laufs gilt."""
    action = decision["action"]
    entry["stale"] = int(decision.get("stale") or 0)
    entry["edge_action"] = action
    entry["edge_note"] = decision.get("why")
    if action in ("adopt", "replace"):
        res = cand
        entry.update({"variant": int(res["variant"]), "status": "tuned" if res.get("params") else "passed"})
        if res.get("params"):
            entry["tuned"] = res["params"]
        else:
            entry.pop("tuned", None)
        entry.pop("ai_proposal", None)
        return res
    if action == "confirm":
        res = active_res
        entry.update({"variant": int(res["variant"]), "status": "tuned" if res.get("params") else "passed"})
        if res.get("params"):
            entry["tuned"] = res["params"]
        entry.pop("ai_proposal", None)
        return res
    if action == "stale_keep":
        # Aktiver Edge bleibt gültig: Parameter/Status unverändert, aktueller
        # (negativer) Lauf nur als Prüfergebnis vermerkt.
        entry["status"] = "tuned"
        if not isinstance(entry.get("tuned"), dict) and active and isinstance(active.get("params"), dict):
            entry["tuned"] = {**active["params"], "name": active.get("name") or "Edge"}
        entry["last_check"] = {"at": _now_iso(), "is": active_res["is"], "oos": active_res["oos"], "passed": False}
        return active_res
    # 'stale' oder 'none': kein Edge fürs Trading
    return None


async def _evaluate_setup(job: Dict, db, cls: str, sid: str, entry: Dict, feats: Dict,
                          split_ts: int, mode: str, ai: Dict, class_passed: int) -> Dict:
    """Ein Setup einer Klasse komplett bewerten: aktiven Edge prüfen, Varianten
    (-Schleife), Feintuning, KI-Revision(en), dann Edge-Entscheidung (edges.decide).
    Gibt {'res', 'entry', 'tried', 'ai_rounds', 'ai_proposal', 'edge', 'store'} zurück;
    `entry` ist der neue Zustand für settings.setup_backtest_state, `store`
    steuert die OOS-Trades fürs Reife-Gate ({'clear': bool, 'trades': [...]})."""
    from services.setup_backtest import revise
    label = ac.LABELS[cls]
    job_id = job["id"]
    n_var = len(detectors.VARIANTS[sid])
    vi = int(entry.get("variant") or 0) % n_var
    history = list(entry.get("history") or [])[-9:]
    tried = 0
    res = None
    active = await edges.get_active(db, cls, sid)
    active_res: Optional[Dict] = None
    cands: List[Dict] = []
    # 1) Vorgemerkter KI-Vorschlag und der AKTIVE Edge (bzw. Alt-Stand): zuerst prüfen
    proposal = entry.get("ai_proposal") if isinstance(entry.get("ai_proposal"), dict) else None
    base_active = active_params_of(sid, entry, active)
    for params, flag in ((proposal, "ai"), (base_active, "active")):
        if not params or (flag == "ai" and res is not None and res["passed"]):
            continue
        job["phase"] = f"{label}: {sid} · {'KI-Vorschlag' if flag == 'ai' else 'aktiven Edge'} prüfen"
        r = await asyncio.to_thread(run_variant, sid, feats, vi, cls, split_ts, job_id, params)
        tried += 1
        meta = entry.get("ai_proposal_meta") if (flag == "ai" and isinstance(entry.get("ai_proposal_meta"), dict)) else {}
        history.append(_hist(r, vi, **{flag: True}, **{k: meta[k] for k in ("reason", "expect", "base") if meta.get(k)}))
        if flag == "active":
            active_res = r
        elif r["passed"]:
            cands.append(r)
        res = r if (res is None or not res["passed"] or (flag == "active" and r["passed"])) else res
    active_ok = bool(active_res and active_res["passed"])
    # 2) Varianten-Schleife. Mit gültigem aktivem Edge werden in den Schleifen-
    #    Modi trotzdem ALLE Basis-Varianten geprüft (bessere Variante finden);
    #    ohne Edge wie bisher bis zum ersten Bestehen.
    if not active_ok or mode in ("loop", "ai_loop"):
        base_fp = edges.fingerprint(base_active) if base_active else None
        for k in range(n_var if (active_ok or mode != "single") else 1):
            cur_vi = (vi + k) % n_var
            if base_fp and edges.fingerprint(detectors.VARIANTS[sid][cur_vi]) == base_fp:
                continue
            r = await asyncio.to_thread(run_variant, sid, feats, cur_vi, cls, split_ts, job_id)
            tried += 1
            history.append(_hist(r, cur_vi))
            if r["passed"]:
                cands.append(r)
            if not active_ok and (res is None or not res["passed"]):
                res, vi = r, cur_vi
            if r["passed"] and not active_ok:
                break
    if res is None:
        res = active_res
    # 2b) Herausforderer aus dem Register (frühere/abgelöste Edges) auf den
    #     aktuellen Daten mitprüfen – ein früher breit bestätigter Satz kann so
    #     den aktiven wieder ablösen (Schleifen-Modi, nur mit aktivem Edge)
    if active and mode in ("loop", "ai_loop"):
        for ch in await edges.challengers(db, cls, sid, active):
            job["phase"] = f"{label}: {sid} · Herausforderer {ch.get('name')} prüfen"
            r = await asyncio.to_thread(run_variant, sid, feats, int(ch.get("variant") or vi), cls, split_ts, job_id,
                                        {**ch["params"], "name": ch.get("name") or "Edge"})
            tried += 1
            history.append(_hist(r, int(ch.get("variant") or vi), challenger=True))
            if r["passed"]:
                cands.append(r)
    # 3) Feintuning (loop/ai_loop) – nur wenn noch kein Satz besteht
    if not res["passed"] and not cands and mode in ("loop", "ai_loop"):
        base_idx = best_base_variant(history)
        if base_idx is not None:
            job["phase"] = f"{label}: {sid} · Feintuning"
            tr = await asyncio.to_thread(tune, sid, feats, base_idx, cls, split_ts, job_id)
            tried += tr["tried"]
            if tr["best"]:
                res = tr["best"]
                history.append(_hist(res, base_idx, tuned=True))
                cands.append(res)
    # 4) KI-Revision: ai_loop testet sofort (bis Ziel/Runden), sonst nur vormerken
    #    – nur wenn in diesem Lauf noch kein Satz besteht (Edge-Verbesserung
    #    bestehender Edges übernimmt der Modus "optimize").
    ai_rounds = 0
    new_proposal = None
    version = int(entry.get("ai_version") or 0)
    if not res["passed"] and not cands and ai["ai_revise"]:
        max_rounds = ai["ai_rounds"] if (mode == "ai_loop" and class_passed < ai["target_passed"]) else 1
        cur_entry = dict(entry)
        while ai_rounds < max_rounds and not res["passed"]:
            if job.get("cancel"):
                raise asyncio.CancelledError()
            version += 1
            job["phase"] = f"{label}: {sid} · KI-Revision {version}"
            prop = await revise.propose(db, cls, sid, cur_entry, history,
                                        rules_for_prompt(res.get("min_trades")), version)
            ai_rounds += 1
            if not prop:
                break
            new_proposal = {**prop, "at": _now_iso()}
            if mode != "ai_loop":
                break
            job["phase"] = f"{label}: {sid} · KI-Revision {version} testen"
            res = await asyncio.to_thread(run_variant, sid, feats, vi, cls, split_ts, job_id, prop["params"])
            tried += 1
            history.append(_hist(res, vi, ai=True, reason=prop.get("reason"), expect=prop.get("expect"),
                                 base=prop.get("base")))
            # Nächste Runde startet vom BESTEN bekannten Satz (analysis.best_entry),
            # nicht blind vom letzten Vorschlag – der Fallback bleibt der letzte Vorschlag.
            cur_entry = {**cur_entry, "ai_proposal": prop["params"]}
            if res["passed"]:
                new_proposal = None
                cands.append(res)
    # 5) Edge-Entscheidung: aktiver Edge vs. bester anderer bestandener Satz
    active_sum = edges.summarize(active_res, "check", int(active_res["variant"])) if active_res else None
    active_ref = ({k: active.get(k) for k in ("params", "fingerprint", "name", "variant", "is", "oos",
                                               "min_trades", "robust", "flags")} | {"passed": True}) if active else None
    if active_ref is None and active_res and active_res["passed"]:
        # Alt-Stand ohne Register: der bestandene bisherige Satz zählt als Kandidat
        cands.insert(0, active_res)
        active_sum = None
    fp_active = (active_ref or {}).get("fingerprint")
    cand_sums = [(r, edges.summarize(r, "ai" if r.get("params") and str(r.get("variant_name", "")).startswith("KI-Rev") else
                                     ("tuned" if r.get("params") else "variant"), int(r["variant"])))
                 for r in cands]
    cand_sums = [(r, s) for r, s in cand_sums if s["fingerprint"] != fp_active]
    cand_sums.sort(key=lambda p: p[1]["robust"], reverse=True)
    ref = active_sum if (active_sum and active_sum.get("passed")) else active_ref
    best_pair = next((p for p in cand_sums if ref is None or edges.better(p[1], ref)[0]),
                     cand_sums[0] if cand_sums else None)
    decision = edges.decide(active_ref, active_sum, best_pair[1] if best_pair else None,
                            stale=int(entry.get("stale") or 0))
    chosen = _apply_decision(entry, decision, active_ref, active_res, best_pair[0] if best_pair else None, vi)
    store = {"clear": True, "trades": []}
    edge_doc = None
    if decision["action"] in ("adopt", "replace"):
        edge_doc = await edges.record(db, cls, sid, best_pair[1], days=int(job["params"].get("days") or DEFAULT_DAYS),
                                      job_id=job_id, oos_trades=chosen["oos_trades"], status="candidate")
        await edges.set_active(db, cls, sid, edge_doc["id"], reason=decision["why"])
        if active_sum:
            await edges.record(db, cls, sid, active_sum, days=int(job["params"].get("days") or DEFAULT_DAYS), job_id=job_id)
        store["trades"] = chosen["oos_trades"]
        res = chosen
    elif decision["action"] == "confirm":
        edge_doc = await edges.record(db, cls, sid, active_sum, days=int(job["params"].get("days") or DEFAULT_DAYS),
                                      job_id=job_id, oos_trades=active_res["oos_trades"], status="active")
        store["trades"] = active_res["oos_trades"]
        res = active_res
    elif decision["action"] == "stale_keep":
        await edges.mark_stale(db, cls, sid, decision["stale"])
        store = {"clear": False, "trades": []}      # letzte bestätigte OOS-Trades behalten
        res = active_res
    elif decision["action"] == "stale":
        await edges.mark_stale(db, cls, sid, decision["stale"])
        entry.update({"status": "stale", "variant": vi})
        entry.pop("tuned", None)
        if new_proposal:
            entry["ai_proposal"] = new_proposal["params"]
            entry["ai_proposal_meta"] = {k: new_proposal.get(k) for k in
                                         ("desc", "reason", "expect", "changes", "base", "model",
                                          "version", "at", "live_revision")}
    else:
        nxt = next_variant(vi, n_var)
        if mode in ("loop", "ai_loop"):
            nxt["status"] = "exhausted"
        entry.update(nxt)
        entry.pop("tuned", None)
        if new_proposal:
            entry["ai_proposal"] = new_proposal["params"]
            entry["ai_proposal_meta"] = {k: new_proposal.get(k) for k in
                                         ("desc", "reason", "expect", "changes", "base", "model",
                                          "version", "at", "live_revision")}
        else:
            entry.pop("ai_proposal", None)
            entry.pop("ai_proposal_meta", None)
    # Alle anderen bestandenen Sätze dieses Laufs ins Register (Kandidaten/Herausforderer)
    for _r, s in cand_sums:
        if s["fingerprint"] != (edge_doc or {}).get("fingerprint"):
            await edges.record(db, cls, sid, s, days=int(job["params"].get("days") or DEFAULT_DAYS), job_id=job_id)
    if cand_sums:
        await edges.prune(db, cls, sid)
    if edge_doc:
        entry.update({"edge_id": edge_doc.get("id"), "robust": edge_doc.get("robust"),
                      "flags": edge_doc.get("flags"), "confirmations": edge_doc.get("confirmations")})
    entry["ai_version"] = version
    shown = res if decision["action"] != "stale_keep" else None
    entry.update({"history": history[-10:], "updated_at": _now_iso(),
                  "symbols": sorted(feats), "signals": res["signals"],
                  "split_at": _iso(split_ts),
                  "lessons": analysis.lessons(history, detectors.param_defaults(sid))})
    if shown is not None:
        entry.update({"name": res["variant_name"], "is": res["is"], "oos": res["oos"],
                      "min_trades": res.get("min_trades"),
                      "diag": {"is": analysis.compact(res["diag"]["is"]), "oos": analysis.compact(res["diag"]["oos"])}})
    edge_info = {"action": decision["action"], "why": decision["why"], "stale": entry.get("stale", 0),
                 "robust": entry.get("robust"), "flags": entry.get("flags"),
                 "confirmations": entry.get("confirmations"),
                 "candidate": (best_pair[1]["name"] if best_pair else None),
                 "candidate_oos": (best_pair[1]["oos"] if best_pair else None)}
    return {"res": res, "entry": entry, "tried": tried, "ai_rounds": ai_rounds,
            "ai_proposal": new_proposal, "edge": edge_info, "store": store}


async def run_job(job_id: str, db, asset_classes: List[str], days: int = DEFAULT_DAYS,
                  mode: str = "single", setups: Optional[List[str]] = None,
                  trigger: str = "manual", ai: Optional[Dict] = None) -> None:
    from services import ai_playbook
    job = JOBS[job_id]
    days = min(max(int(days or DEFAULT_DAYS), 14), MAX_DAYS)
    mode = normalize_mode(mode)
    ai = ai_options(ai)
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
                class_passed = 0
                for sid in sids:
                    if job.get("cancel"):
                        raise asyncio.CancelledError()
                    step += 1
                    job["progress"] = int(step / total_steps * 100)
                    job["phase"] = f"{label}: {sid}"
                    if mode == "optimize":
                        # Optimierer: NUR Setups mit validiertem Edge (Backtest
                        # bestanden ODER mit echten Daten live-reif) anfassen.
                        cur = dict(cls_state.get(sid) or {})
                        has_edge = cur.get("status") in PASSED_STATES \
                            or bool((cls_pb.get("live_ready") or {}).get(sid))
                        if not has_edge:
                            rows.append({"asset_class": cls, "setup": sid,
                                         "status": "no_edge_skip",
                                         "note": "kein validierter Edge – Optimierer überspringt"})
                            continue
                        ev = await _optimize_setup(job, db, cls, sid, cur, feats, split_ts)
                        res, entry = ev["res"], ev["entry"]
                        cls_state[sid] = entry
                        if res["passed"] and res["oos_trades"]:
                            await db[weights.COLLECTION].delete_many(
                                {"asset_class": cls, "setup": sid})
                            now = _now_iso()
                            await db[weights.COLLECTION].insert_many(
                                [{**t, "run_at": now} for t in res["oos_trades"]])
                        base_o = (ev["base"].get("oos") or {})
                        rows.append({"asset_class": cls, "setup": sid,
                                     "status": "optimized" if ev["improved"] else "kept",
                                     "variant": res["variant_name"],
                                     "variant_idx": int(entry.get("variant") or 0),
                                     "variants_total": res["variants_total"],
                                     "tried": ev["tried"], "is": res["is"], "oos": res["oos"],
                                     "signals": res["signals"], "min_trades": res.get("min_trades"),
                                     "base_oos": base_o,
                                     "stored": len(res["oos_trades"]) if res["passed"] else 0,
                                     "symbols": sorted(feats), "ai_rounds": 0,
                                     "diag": {"is": analysis.compact(res["diag"]["is"]),
                                              "oos": analysis.compact(res["diag"]["oos"])},
                                     "lessons": [], "ai_proposal": None,
                                     "edge": {"action": entry.get("edge_action"), "why": entry.get("edge_note"),
                                              "robust": entry.get("robust"), "flags": entry.get("flags"),
                                              "confirmations": entry.get("confirmations"), "stale": entry.get("stale", 0)},
                                     "note": (f"OOS {base_o.get('trades', 0)}T/{base_o.get('winrate', 0)}% "
                                              f"→ {res['oos'].get('trades', 0)}T/{res['oos'].get('winrate', 0)}%"
                                              if ev["improved"] else "kein besserer Parameter-Satz gefunden")})
                        continue
                    ready_real = bool((cls_pb.get("live_ready") or {}).get(sid)) \
                        and sid not in (cls_pb.get("bt_promoted") or {})
                    if ready_real:
                        rows.append({"asset_class": cls, "setup": sid, "status": "live",
                                     "note": "bereits mit echten Daten live-reif – nicht angefasst"})
                        continue
                    ev = await _evaluate_setup(job, db, cls, sid, dict(cls_state.get(sid) or {}),
                                               feats, split_ts, mode, ai, class_passed)
                    res, entry = ev["res"], ev["entry"]
                    if res["passed"]:
                        class_passed += 1
                    cls_state[sid] = entry
                    store = ev.get("store") or {"clear": True, "trades": []}
                    if store.get("clear"):
                        await db[weights.COLLECTION].delete_many({"asset_class": cls, "setup": sid})
                    if store.get("trades"):
                        now = _now_iso()
                        await db[weights.COLLECTION].insert_many(
                            [{**t, "run_at": now, "edge_id": entry.get("edge_id")} for t in store["trades"]])
                    rows.append({"asset_class": cls, "setup": sid, "status": entry["status"],
                                 "variant": res["variant_name"], "variant_idx": int(entry.get("variant") or 0),
                                 "variants_total": res["variants_total"], "tried": ev["tried"],
                                 "is": res["is"], "oos": res["oos"], "signals": res["signals"],
                                 "min_trades": res.get("min_trades"),
                                 "stored": len(store.get("trades") or []),
                                 "symbols": sorted(feats), "ai_rounds": ev["ai_rounds"],
                                 "edge": ev.get("edge"),
                                 "diag": entry.get("diag"), "lessons": entry.get("lessons") or [],
                                 "ai_proposal": ({k: v for k, v in ev["ai_proposal"].items() if k != "params"}
                                                 if ev["ai_proposal"] else None)})
                classes_state[cls] = cls_state
                if mode == "optimize":
                    n_imp = sum(1 for r in rows if r.get("asset_class") == cls and r.get("status") == "optimized")
                    n_kept = sum(1 for r in rows if r.get("asset_class") == cls and r.get("status") == "kept")
                    if n_imp or n_kept:
                        await _feed(db, (f"Setup-Optimierer {label} ({days} Tage): {n_imp + n_kept} "
                                         f"Edge-Setups geprüft, {n_imp} verbessert (mehr Trades/"
                                         f"höhere Winrate bei mindestens gleichem OOS-PnL), "
                                         f"{n_kept} unverändert (kein besserer Satz)."),
                                    asset_class=cls)
                    continue
                n_pass = sum(1 for r in rows if r.get("asset_class") == cls and r.get("status") in PASSED_STATES)
                n_keep = sum(1 for r in rows if r.get("asset_class") == cls
                             and (r.get("edge") or {}).get("action") in ("stale_keep", "confirm"))
                n_repl = sum(1 for r in rows if r.get("asset_class") == cls
                             and (r.get("edge") or {}).get("action") == "replace")
                n_test = sum(1 for r in rows if r.get("asset_class") == cls and r.get("is"))
                n_prop = sum(1 for r in rows if r.get("asset_class") == cls and r.get("ai_proposal"))
                if n_test:
                    await _feed(db, (f"Backtest-Seeding {label} ({days} Tage, {MODE_LABELS.get(mode, mode)}): "
                                     f"{n_test} Setups getestet, {n_pass} mit Edge in In- und Out-of-Sample"
                                     + (f", {n_prop} KI-Revisionen für den nächsten Lauf vorgemerkt" if n_prop else "")
                                     + (f", {n_keep} bestehende Edges bestätigt/behalten" if n_keep else "")
                                     + (f", {n_repl} durch robustere Sätze ersetzt" if n_repl else "")
                                     + f". Backtest-Trades zählen ×{weights.BACKTEST_WEIGHT:g} und gedeckelt fürs "
                                     f"Reife-Gate – Live erst nach {weights.MIN_REAL_TRADES}+ profitablen echten "
                                     "Paper-Trades."),
                                asset_class=cls)
        state["classes"] = classes_state
        summary = {"kind": "ai_seed", "mode": mode, "days": days, "asset_classes": asset_classes,
                   "rows": rows, "finished_at": _now_iso(), "ai": ai,
                   "passed": sum(1 for r in rows if r.get("status") in PASSED_STATES + ("optimized",)),
                   "tested": sum(1 for r in rows if r.get("is")),
                   "optimized": sum(1 for r in rows if r.get("status") == "optimized"),
                   "ai_proposals": sum(1 for r in rows if r.get("ai_proposal")),
                   "ai_rounds": sum(int(r.get("ai_rounds") or 0) for r in rows), "trigger": trigger,
                   "data_notes": list(dict.fromkeys(job.get("data_notes") or []))[:12]}
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


MODE_LABELS = {"single": "Einmal-Durchlauf", "loop": "Auto-Schleife", "ai_loop": "KI-Schleife",
               "optimize": "Setup-Optimierer"}


async def overview(db) -> Dict:
    """Für UI/API: Zustand je Klasse × Setup, testbare Setups, Regeln."""
    from services import ai_playbook
    state = await load_state(db)
    library = ai_playbook.all_setups()
    from services.setup_backtest import auto as seed_auto
    return {"classes": state.get("classes") or {},
            "last_result": state.get("last_result"),
            "auto": seed_auto.public_state(state.get("auto")),
            "eligible": {cls: eligible_setups(cls, library) for cls in ac.CLASSES},
            "not_backtestable": detectors.NOT_BACKTESTABLE,
            "variants": {sid: [v["name"] for v in vs] for sid, vs in detectors.VARIANTS.items()},
            "param_help": {sid: detectors.param_help(sid) for sid in detectors.VARIANTS},
            "min_trades": {cls: {sid: min_trades_for(sid, len(ac.symbols_of(cls)), DEFAULT_DAYS)
                                 for sid in eligible_setups(cls, library)} for cls in ac.CLASSES},
            "modes": list(MODES),
            "ai_defaults": ai_options(None),
            "rules": {"weight": weights.BACKTEST_WEIGHT, "ttl_days": weights.BACKTEST_TTL_DAYS,
                      "min_real_trades": weights.MIN_REAL_TRADES,
                      "max_backtest_weighted": weights.MAX_BACKTEST_WEIGHTED,
                      "min_oos_trades": MIN_OOS_TRADES, "min_is_trades": MIN_IS_TRADES,
                      "is_share": IS_SHARE, "default_days": DEFAULT_DAYS, "max_days": MAX_DAYS,
                      "edge_stale_max": edges.STALE_MAX, "edge_replace_margin": edges.REPLACE_MARGIN,
                      "edge_min_trades_ratio": edges.MIN_TRADES_RATIO},
            "running": running_job() is not None}
