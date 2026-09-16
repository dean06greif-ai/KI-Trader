"""Event-Setup-Backtests gebündelt im Backtester (FOMC + CPI/NFP/PPI/PCE).

Orchestriert die bestehenden Event-Backtests (fomc_backtest / econ_backtest)
als EINEN Hintergrund-Job mit freier Event-Auswahl und optionaler KI-Schleife:
schlägt die Validierung fehl, überarbeitet der Forschungs-Analyst die
Event-Parameter (nur innerhalb fester Grenzen, PARAM_BOUNDS) und der Backtest
läuft erneut – wie die KI-Schleife des Setup-Backtests. Der Overfitting-Schutz
bleibt unangetastet: Validierung verlangt weiterhin positives Gesamt- UND
Out-of-Sample-PnL (fomc_backtest.validated). KI-revidierte Parameter werden
je Event in db.settings persistiert und beim nächsten Lauf als Basis genutzt.
"""
import logging
from datetime import datetime, timezone

from services import econ_backtest, econ_event, fomc_backtest

logger = logging.getLogger(__name__)

EVENT_KEYS = ("fomc", "cpi", "nfp", "ppi", "pce")
EVENT_LABELS = {"fomc": "FOMC", "cpi": "CPI", "nfp": "NFP", "ppi": "PPI", "pce": "PCE"}
ROLE = "research_analyst"

# Erlaubte Bereiche je Parameter – die KI darf NUR innerhalb dieser Grenzen
# revidieren (Overfitting-/Unsinns-Bremse).
PARAM_BOUNDS: dict[str, tuple] = {
    "pre_range_hours": (1.0, 6.0),
    "fade_window_min": (10, 45),
    "fade_spike_mult": (0.15, 0.8),
    "fade_sl_buffer_mult": (0.05, 0.3),
    "drift_start_min": (10, 60),
    "drift_window_min": (45, 180),
    "drift_break_mult": (0.1, 0.6),
    "drift_tp_mult": (0.5, 2.0),
    "timeout_fade_min": (60, 240),
    "timeout_drift_min": (90, 300),
    "min_range_pct": (0.05, 0.5),
}
MAX_ROUNDS = 5

SYSTEM = (
    "Du bist der Forschungs-Analyst im KI-Team einer Trading-Plattform. "
    "Du überarbeitest die Parameter eines EVENT-Setups (Whipsaw-Fade + Drift um "
    "Makro-Ereignisse), dessen Backtest die Validierung nicht bestanden hat. "
    "Bleibe STRENG innerhalb der erlaubten Bereiche. Ändere nur wenige Schlüssel "
    "gezielt und begründe knapp. Antworte AUSSCHLIESSLICH mit validem JSON:\n"
    '{"params": {"schlüssel": wert, ...}, "reason": "kurz warum", '
    '"expect": "erwartete Wirkung"}'
)

# Ein Batch-Job zur Zeit (Modul-Zustand wie bei fomc_backtest)
JOB: dict = {"status": "idle"}


def params_id(key: str) -> str:
    return f"{key}_event_params_ai"


def base_params(key: str) -> dict:
    if key == "fomc":
        return dict(fomc_backtest.FIXED)
    return dict(econ_backtest.FIXED.get(key) or econ_backtest._DATA_RELEASE_FIXED)


def sanitize_params(raw, base: dict) -> dict | None:
    """Nur bekannte Schlüssel, hart auf PARAM_BOUNDS geklemmt; None wenn kein
    gültiger/veränderter Vorschlag (rein & testbar)."""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    for k, v in raw.items():
        if k not in PARAM_BOUNDS:
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        lo, hi = PARAM_BOUNDS[k]
        val = max(lo, min(hi, val))
        if isinstance(base.get(k), int) and float(val).is_integer():
            val = int(val)
        out[k] = round(val, 4) if isinstance(val, float) else val
    merged = {**base, **out}
    if not out or all(merged[k] == base.get(k) for k in out):
        return None
    return out


def score(result: dict | None) -> float:
    """Vergleichs-Score eines Laufs: OOS-PnL 60 %, Gesamt-PnL 40 % (rein)."""
    agg = (result or {}).get("aggregate") or {}
    oos = (agg.get("out_of_sample") or {}).get("pnl", 0) or 0
    tot = (agg.get("total") or {}).get("pnl", 0) or 0
    return round(0.6 * oos + 0.4 * tot, 4)


def param_diff(base: dict, new: dict) -> list[str]:
    return [f"{k}: {base.get(k)} → {v}" for k, v in new.items() if base.get(k) != v]


def event_running(key: str) -> bool:
    if key == "fomc":
        return fomc_backtest.is_running()
    return econ_backtest.is_running(key)


def job_running() -> bool:
    return JOB.get("status") == "running"


def job_public() -> dict:
    return {k: JOB.get(k) for k in ("status", "progress", "phase", "events",
                                    "results", "started_at", "finished_at", "error")}


async def stored_params(db, key: str) -> dict | None:
    doc = await db.settings.find_one({"_id": params_id(key)})
    return (doc or {}).get("params") or None


async def stored_params_meta(db, key: str) -> dict | None:
    doc = await db.settings.find_one({"_id": params_id(key)})
    if not doc:
        return None
    doc.pop("_id", None)
    return doc


async def save_params(db, key: str, proposal: dict, result: dict) -> None:
    await db.settings.update_one({"_id": params_id(key)}, {"$set": {
        "params": proposal["params"],
        "reason": proposal.get("reason", ""),
        "changes": proposal.get("changes", []),
        "model": proposal.get("model", ""),
        "version": int(proposal.get("version") or 1),
        "validated": bool(result.get("validated")),
        "score": score(result),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }}, upsert=True)


async def reset_params(db, key: str) -> None:
    await db.settings.delete_one({"_id": params_id(key)})


async def last_result_light(db, key: str) -> dict | None:
    """Ergebnis OHNE Einzeltrades (Payload-schonend für die Übersicht)."""
    rid = fomc_backtest.RESULT_ID if key == "fomc" else econ_backtest.result_id(key)
    doc = await db.settings.find_one({"_id": rid}, {"trades": 0})
    if doc:
        doc.pop("_id", None)
    return doc


async def _run_single(db, key: str, years: float, params: dict | None) -> dict:
    if key == "fomc":
        return await fomc_backtest.run(db, years=years, params=params)
    return await econ_backtest.run(db, key, years=years, params=params)


async def _propose(db, key: str, params_now: dict, result: dict,
                   lessons: list[str], version: int) -> dict | None:
    from services.ai_engine import ai_engine
    if not getattr(ai_engine, "key", None):
        logger.info("Event-KI-Schleife: kein LLM-Key – Revision übersprungen")
        return None
    agg = result.get("aggregate") or {}
    rng = "\n".join(f"- {k}: {lo}–{hi} (aktuell {params_now.get(k)})"
                    for k, (lo, hi) in PARAM_BOUNDS.items())
    per_ev = result.get("per_event") or {}
    ev_lines = "\n".join(f"- {d}: {e.get('trades')}T, PnL {e.get('pnl'):+.2f}$"
                         for d, e in list(per_ev.items())[-10:])
    lesson_txt = "\n".join(lessons[-5:]) or "- noch keine Revision getestet"
    prompt = (
        f"EVENT-SETUP '{EVENT_LABELS.get(key, key)}' – Backtest NICHT validiert: "
        f"{result.get('validation_reason', '?')}\n\n"
        f"Statistik: Gesamt {agg.get('total')}\nIn-Sample {agg.get('in_sample')}\n"
        f"Out-of-Sample {agg.get('out_of_sample')}\n\nLetzte Events:\n{ev_lines or '- keine'}\n\n"
        f"LERNSCHLEIFE bisheriger Revisionen:\n{lesson_txt}\n\n"
        f"Erlaubte Parameter & Bereiche:\n{rng}\n\n"
        "Regeln: A) Whipsaw-Fade – Spike über Pre-Range, der zurück schließt -> Gegenposition. "
        "B) Drift – nachhaltiger Schluss jenseits der Pre-Range -> Trendrichtung.\n"
        "Validierung: Gesamt- UND Out-of-Sample-PnL müssen positiv sein.\n"
        "Gib EINEN revidierten Parameter-Satz (nur geänderte Schlüssel) als JSON zurück."
    )
    try:
        text, _prov, model = await ai_engine.generate_for_role(ROLE, prompt, SYSTEM,
                                                               temperature=0.3)
        data = ai_engine._parse_json(text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Event-KI-Revision {key} fehlgeschlagen: {e}")
        return None
    params = sanitize_params(data.get("params"), params_now)
    if not params:
        logger.info(f"Event-KI-Revision {key}: kein gültiger/veränderter Vorschlag")
        return None
    return {"params": {**params_now, **params},
            "reason": str(data.get("reason") or "").strip()[:240],
            "expect": str(data.get("expect") or "").strip()[:240],
            "changes": param_diff(params_now, params),
            "model": model, "version": version}


async def run_batch(db, events: list[str], years: float = 2.0,
                    ai_revise: bool = False, ai_rounds: int = 2) -> None:
    global JOB
    keys = [k for k in events if k in EVENT_KEYS]
    JOB = {"status": "running", "progress": 0, "phase": "Startet…", "events": keys,
           "results": {}, "started_at": datetime.now(timezone.utc).isoformat(),
           "finished_at": None, "error": None}
    ai_rounds = max(1, min(MAX_ROUNDS, int(ai_rounds or 1)))
    try:
        for i, key in enumerate(keys):
            label = EVENT_LABELS.get(key, key)
            JOB["phase"] = f"{label}: Backtest läuft…"
            JOB["progress"] = round(i / len(keys) * 100)
            base = base_params(key)
            params_now = {**base, **(await stored_params(db, key) or {})}
            use_override = params_now != base
            res = await _run_single(db, key, years,
                                    params_now if use_override else None)
            if res.get("status") == "busy":
                JOB["results"][key] = {"error": res.get("detail"), "rounds": 0}
                continue
            rounds, lessons = 0, []
            version = ((await stored_params_meta(db, key)) or {}).get("version", 0)
            while (ai_revise and not res.get("validated") and rounds < ai_rounds
                   and res.get("status") == "ok"):
                rounds += 1
                version += 1
                JOB["phase"] = f"{label}: KI-Revision {rounds}/{ai_rounds}…"
                proposal = await _propose(db, key, params_now, res, lessons, version)
                if not proposal:
                    break
                JOB["phase"] = f"{label}: Re-Test KI-Rev.{version}…"
                res2 = await _run_single(db, key, years, proposal["params"])
                if res2.get("status") != "ok":
                    break
                improved = res2.get("validated") or score(res2) > score(res)
                lessons.append(
                    f"- Rev.{version} ({'; '.join(proposal['changes'][:3])}): "
                    f"Score {score(res)} → {score(res2)}"
                    f"{' · VALIDIERT' if res2.get('validated') else ''}"
                    f"{' · verworfen' if not improved else ''}")
                if improved:
                    params_now = proposal["params"]
                    res = res2
                    await save_params(db, key, proposal, res2)
                else:
                    # schlechtere Revision: alten (besseren) Stand erneut persistieren,
                    # damit Ergebnis-Doc + Live-Validierung zum besten Lauf passen
                    await _run_single(db, key, years,
                                      params_now if params_now != base else None)
            agg = res.get("aggregate") or {}
            JOB["results"][key] = {
                "validated": bool(res.get("validated")),
                "reason": res.get("validation_reason", ""),
                "score": score(res), "rounds": rounds, "lessons": lessons,
                "trades": (agg.get("total") or {}).get("trades", 0),
                "ai_params": params_now != base,
            }
        JOB["progress"] = 100
        JOB["phase"] = "Fertig"
        JOB["status"] = "done"
    except Exception as e:  # noqa: BLE001
        logger.error(f"Event-Backtest-Batch fehlgeschlagen: {e}")
        JOB["status"] = "error"
        JOB["error"] = str(e)[:300]
    finally:
        JOB["finished_at"] = datetime.now(timezone.utc).isoformat()


def status_snapshots() -> dict[str, dict]:
    """Alle Event-Snapshots (FOMC + Daten-Events) in einheitlicher Form."""
    from services import fomc_event
    fomc = fomc_event.status_snapshot()
    fomc.update({"event": "fomc", "label": "FOMC",
                 "next_release_utc": fomc.get("next_decision_utc"),
                 "minutes_to_release": fomc.get("minutes_to_decision")})
    out = {"fomc": fomc}
    out.update(econ_event.status_snapshots())
    return out
