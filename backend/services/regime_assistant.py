"""Regime-Assistent je Anlageklasse (ANALYSE_DYNAMIK_REGIME_0626 E5).

EIN Knopf statt 9 Stufen: festes Rezept je Anlageklasse (services.regime_hierarchy,
Jump-Modell, keine Parameter-Suche -> kein Such-Overfitting). Für jedes Asset
wird die Nutzen-Note (services.regime_usefulness) berechnet – für das
Haupt-Regime, das Makro-Regime und die gefilterte Kombination – und zu einer
Ampel je Anlageklasse zusammengefasst.

Optional (Standard AUS): `fallback_enabled` – solange es für eine Anlageklasse
keine Lab-Freigabe gibt, liefert der Assistent bei Ampel grün/gelb das
Struktur-Regime für den KI-Trader (structural_regime, Quelle `regime_assistant`).
"""
import asyncio
import logging
import statistics
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

from services import regime_hierarchy as rh
from services import regime_usefulness as ru

logger = logging.getLogger(__name__)

CONFIG_ID = "regime_assistant"
MAX_SYMBOLS = 8
KEEP_RUNS = 20
RECENT_FRACTION = 0.3
VARIANTS = (("combined", "Kombiniert (Makro × Haupt)"), ("primary", "Nur Haupt-Regime"),
            ("macro", "Nur Makro-Regime"))
FALLBACK_LIGHTS = ("grün", "gelb")

JOBS: Dict[str, Dict] = {}
_config: Dict = {"fallback_enabled": False}
_latest: Dict[str, Dict] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def config() -> Dict:
    return dict(_config)


async def load_config(db) -> Dict:
    try:
        doc = await db.app_config.find_one({"id": CONFIG_ID}, {"_id": 0})
        if doc:
            _config["fallback_enabled"] = bool(doc.get("fallback_enabled"))
        async for run in db.regime_assistant_runs.find({}, {"_id": 0, "results": 0}).sort(
                "created_at", -1).limit(KEEP_RUNS):
            _latest.setdefault(run["asset_class"], run)
    except Exception as e:  # noqa: BLE001 – fail-soft
        logger.warning(f"regime_assistant laden: {e}")
    return config()


async def save_config(db, body: Dict) -> Dict:
    if "fallback_enabled" in (body or {}):
        _config["fallback_enabled"] = bool(body["fallback_enabled"])
    await db.app_config.update_one({"id": CONFIG_ID}, {"$set": {"id": CONFIG_ID, **_config}},
                                   upsert=True)
    from services import structural_regime
    structural_regime.invalidate()
    return config()


def latest_summary(asset_class: str) -> Optional[Dict]:
    return _latest.get(asset_class)


def fallback_allowed(asset_class: str) -> bool:
    run = _latest.get(asset_class) or {}
    return bool(_config.get("fallback_enabled")) and \
        ((run.get("summary") or {}).get("light") in FALLBACK_LIGHTS)


# ---------------- Bewertung (rein) ----------------
def evaluate_symbol(res: Dict, bpd: float) -> Dict:
    """Nutzen-Note je Variante auf den Kerzen nach dem Warmup beider Ebenen."""
    closes = [float(c["close"]) for c in res["_primary_candles"]]
    p, m = np.asarray(res["_primary_series"]), np.asarray(res["_macro_series"])
    known = np.where((p != -9) & (m != -9))[0]
    start = int(known[0]) if len(known) else len(closes)
    series = {"primary": p, "macro": m, "combined": rh.combined_series(m, p)}
    recent = max(int(len(closes) - (len(closes) - start) * RECENT_FRACTION), start)
    out = {}
    for key, _label in VARIANTS:
        full = ru.evaluate(closes, series[key], bpd, start)
        rec = ru.evaluate(closes, series[key], bpd, recent)
        out[key] = {"score": full["score"], "grade": full["grade"], "light": full["light"],
                    "few_phases": full["few_phases"], "stability": full["stability"],
                    "horizons": [{k: h.get(k) for k in ("days", "separation_z", "sign_hit_pct",
                                                        "fwd_pct")} for h in full["horizons"]],
                    "share_pct": full["share_pct"], "recent_score": rec["score"]}
    return {"days_evaluated": round((len(closes) - start) / bpd, 1), "variants": out}


def summarize(rows: List[Dict]) -> Dict:
    """Ampel je Anlageklasse (rein): Median der Noten je Variante, beste Variante."""
    med = {}
    for key, _ in VARIANTS:
        vals = [r["eval"]["variants"][key]["score"] for r in rows
                if r.get("eval") and r["eval"]["variants"][key]["score"] is not None]
        med[key] = round(statistics.median(vals), 1) if vals else None
    cand = [(k, v) for k, v in med.items() if v is not None]
    if not cand:
        return {"scores": med, "best": None, "light": "grau", "grade": "zu wenig Daten",
                "recommendation": "Zu wenig Daten – Zeitraum erhöhen oder andere Assets wählen."}
    # Kombination bevorzugen, wenn sie höchstens 3 Punkte schwächer ist (Makro-Schutz)
    best_k, best_v = max(cand, key=lambda kv: kv[1])
    if med.get("combined") is not None and best_v - med["combined"] <= 3.0:
        best_k, best_v = "combined", med["combined"]
    light = ru.light(best_v)
    label = dict(VARIANTS)[best_k]
    rec = {"grün": f"Gut nutzbar: {label}. Als Struktur-Filter für KI-Trader und dynamische "
                   "Strategien geeignet – erst im Paper beobachten.",
           "gelb": f"Mittel: {label} trennt die Marktphasen erkennbar, aber nicht stark. "
                   "Nur als Filter/Größen-Faktor nutzen, nicht für harte Strategie-Wechsel.",
           "rot": "Schwach: Die Erkennung trennt die Marktphasen hier kaum – lieber ohne "
                  "Regime-Filter handeln."}[light]
    return {"scores": med, "best": best_k, "best_label": label, "light": light,
            "grade": ru.grade(best_v), "recommendation": rec}


# ---------------- Job ----------------
def create_job(params: Dict) -> str:
    jid = uuid.uuid4().hex[:12]
    JOBS[jid] = {"id": jid, "status": "running", "progress": 0, "phase": "Start",
                 "params": params, "created_at": _now(), "result": None, "error": None}
    return jid


def running() -> Optional[Dict]:
    return next((j for j in JOBS.values() if j["status"] == "running"), None)


async def run(job_id: str, db) -> None:
    from services import regime_lab as lab
    from services import setup_asset_class
    from services.timeframes import tf_minutes
    job = JOBS[job_id]
    p = job["params"]
    cls = p["asset_class"]
    rc = rh.recipe_for(cls)
    syms = [s for s in (p.get("symbols") or setup_asset_class.symbols_of(cls))][:MAX_SYMBOLS]
    lead = rc.get("leader")
    if lead and lead in syms:
        syms = [lead] + [s for s in syms if s != lead]
    days = int(p.get("days") or rc["days"])
    bpd = 1440.0 / tf_minutes(rc["primary_tf"])
    rows: List[Dict] = []
    leader_macro = None
    try:
        for i, sym in enumerate(syms):
            job["phase"] = f"{sym}: {days} Tage {rc['primary_tf']}-Kerzen laden & bewerten ({i + 1}/{len(syms)})"
            job["progress"] = round(i / max(len(syms), 1) * 95)
            try:
                hist = await lab.fetch_histories([sym], days, rc["primary_tf"])
                candles = hist.get(sym) or []
                if len(candles) < 120:
                    raise RuntimeError(f"zu wenig Kerzen ({len(candles)})")
                res = await asyncio.to_thread(rh.analyze_candles, sym, cls, candles, leader_macro)
                if sym == lead:
                    leader_macro = (res.get("macro") or {}).get("direction")
                ev = await asyncio.to_thread(evaluate_symbol, res, bpd)
                rows.append({"symbol": sym, "bars": len(candles), **rh.public(res), "eval": ev})
            except Exception as e:  # noqa: BLE001 – einzelnes Asset isolieren
                logger.warning(f"regime_assistant {sym}: {e}")
                rows.append({"symbol": sym, "error": str(e)[:200]})
        summary = summarize(rows)
        doc = {"id": job_id, "asset_class": cls, "created_at": _now(), "days": days,
               "recipe": rh.public_recipe(cls), "summary": summary, "results": rows}
        await db.regime_assistant_runs.insert_one(dict(doc))
        old = await db.regime_assistant_runs.find({}, {"_id": 0, "id": 1}).sort(
            "created_at", -1).skip(KEEP_RUNS).to_list(100)
        if old:
            await db.regime_assistant_runs.delete_many({"id": {"$in": [o["id"] for o in old]}})
        _latest[cls] = {k: v for k, v in doc.items() if k != "results"}
        from services import structural_regime
        structural_regime.invalidate()
        job.update({"status": "done", "progress": 100, "phase": "Fertig", "result": doc})
    except Exception as e:  # noqa: BLE001
        logger.exception("regime_assistant run")
        job.update({"status": "error", "error": str(e)[:300], "phase": "Fehler"})


# ---------------- Struktur-Fallback für den KI-Trader ----------------
async def structural_fallback(symbol: str) -> Optional[Dict]:
    """MarketContext(structural) aus dem Assistenten – nur wenn eingeschaltet und
    die Ampel der Anlageklasse grün/gelb ist. Sonst None (Alt-Verhalten)."""
    from services import market_context as mc
    from services import setup_asset_class
    cls = setup_asset_class.asset_class_of(symbol)
    if not fallback_allowed(cls):
        return None
    res = await rh.resolve(symbol)
    if res.get("error"):
        return None
    comb = res.get("combined") or {}
    pr = res.get("primary") or {}
    direction = pr.get("direction")
    ctx = mc.structural_context("regime_assistant", direction=direction,
                                label=comb.get("label"), confidence=pr.get("confidence"),
                                model_fp=f"assist-{cls}-{res['recipe']['macro_tf']}-{res['recipe']['primary_tf']}")
    since = pr.get("since_ts")
    ctx.update({"stage": "assistant", "timeframe": res["recipe"]["primary_tf"], "kept": True,
                "since_days": round((datetime.now(timezone.utc).timestamp() * 1000 - since) / 86400000, 1)
                if since else None,
                "size_factor": comb.get("size_factor"), "macro": res.get("macro"),
                "assistant_light": ((_latest.get(cls) or {}).get("summary") or {}).get("light")})
    return ctx


# ---------------- Hintergrund: Makro-Zustand für Filter aktuell halten ----------------
REFRESH_EVERY_S = 900


async def filter_symbols(db) -> List[str]:
    syms = set()
    async for d in db.dynamic_strategies.find(
            {"settings.macro_filter_enabled": True, "archived": {"$ne": True}},
            {"_id": 0, "symbols": 1}):
        syms.update(d.get("symbols") or [])
    return sorted(syms)


async def run_loop(db) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            for sym in await filter_symbols(db):
                await rh.resolve(sym)
        except Exception as e:  # noqa: BLE001 – Loop darf nie sterben
            logger.warning(f"regime_assistant loop: {e}")
        await asyncio.sleep(REFRESH_EVERY_S)
