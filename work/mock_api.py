"""Mock-API für UI-Screenshots des Regime-Labs (kein Zugriff auf Produktion).
Erzeugt eine echte Analyse über services.regime_lab.run_analysis auf
synthetischen Kerzen und liefert die Regime-Lab-Endpoints read-only."""
import asyncio
import os
import sys

sys.path.insert(0, "/app/work/kitrader/backend")
os.environ.setdefault("JWT_SECRET", "x" * 64)

import numpy as np  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from services import regime_lab as lab  # noqa: E402
from services import regime_quality as rq  # noqa: E402
from services import regime_truth as rt  # noqa: E402
from services import regime_engine as eng  # noqa: E402


def make_candles(closes, ts0=1_700_000_000_000, step_ms=3_600_000, noise=0.004, seed=7):
    rng = np.random.default_rng(seed)
    out = []
    for i, c in enumerate(closes):
        c = float(c) * (1.0 + rng.normal(0, noise))
        out.append({"timestamp": ts0 + i * step_ms, "open": c * 0.999, "high": c * 1.004,
                    "low": c * 0.996, "close": c, "volume": 100.0 + rng.uniform(0, 50)})
    return out


def synth_closes(segments, start=100.0, bpd=24):
    closes = [start]
    for days, drift in segments:
        per_bar = (1.0 + drift / 100.0) ** (1.0 / bpd)
        for _ in range(int(days * bpd)):
            closes.append(closes[-1] * per_bar)
    return closes


SEGS = [(20, 1.2), (15, 0.0), (20, -1.1), (10, 0.0), (25, 0.9), (15, -0.8), (15, 0.0), (20, 1.0),
        (12, -1.3), (18, 0.0), (22, 1.1)]
HIST = {"BTCUSDT": make_candles(synth_closes(SEGS), seed=1),
        "ETHUSDT": make_candles(synth_closes(SEGS, start=50), seed=2, noise=0.006)}


async def _fake_fetch(symbols, days, timeframe, job=None, **_):
    return {s: HIST[s] for s in symbols if s in HIST}


lab.fetch_histories = _fake_fetch
lab.eng = eng

DOC = {}
CAL = []


async def build():
    jid = lab.create_job("analysis", {})
    await lab.run_analysis(jid, {"symbols": list(HIST), "timeframe": "1h", "days": 190, "scope": "both",
                                 "train_pct": 75, "engine": "v2", "engine_config": {"regime_mode": 5},
                                 "name": "Demo · 1h · 190d · BTC+ETH"}, None)
    job = lab.JOBS[jid]
    assert job["status"] == "done", job
    DOC.update(job["result"]["analysis_doc"])
    for det in ("reactive", "kombi"):
        rep = rt.calibrate(HIST, "1h", {"detector": det, "regime_mode": 5}, "centered")
        rep.pop("per_symbol", None)
        CAL.append({"id": f"cal_{det}", "created_at": "2026-06-10T09:12:00+00:00",
                    "symbols": list(HIST), "timeframe": "1h", "report": rep})


app = FastAPI()


@app.on_event("startup")
async def _startup():
    await build()

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/coins")
def coins():
    return {"coins": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "GOLD", "EURUSD"]}


@app.get("/api/strategies")
def strategies():
    return {"strategies": [{"id": "s1", "name": "Demo-Strategie"}]}


@app.get("/api/regime-lab/engine/defaults")
def defaults():
    return {"engine": eng.ENGINE, **eng.engine_defaults()}


@app.get("/api/regime-lab/list")
def lst():
    return {"analyses": [{"id": DOC["id"], "name": DOC["name"], "symbols": DOC["symbols"], "timeframe": "1h",
                          "days": 190, "scope": "both", "settings": DOC["settings"], "created_at": DOC["created_at"],
                          "n_regimes_combined": len(DOC["combined"]["model"]["regimes"]), "n_assignments": 0,
                          "has_walkforward": False, "walkforward_passed": False, "walkforward_stale": False,
                          "dataset_status": "pinned", "release": {"stage": "none"}}]}


@app.get("/api/regime-lab/calibrations")
def cals():
    return {"calibrations": CAL}


@app.get("/api/regime-lab/active")
def active():
    return {"active": None}


@app.get("/api/regime-lab/releases")
def releases():
    return {"releases": [], "activation": {"shadow_trades": 0}}


@app.get("/api/localworker/status")
def lw():
    return {"online": True}


@app.post("/api/regime-lab/calibrate")
def start_cal():
    return {"status": "started", "job_id": "cal_kombi"}


@app.get("/api/regime-lab/status/{jid}")
def status(jid: str):
    cal = next((c for c in CAL if c["id"] == jid), None)
    if not cal:
        return JSONResponse({"detail": "Job nicht gefunden"}, status_code=404)
    return {"id": jid, "kind": "calibration", "status": "done", "progress": 100, "phase": "Fertig",
            "result": {"kind": "calibration", "report": cal["report"]}}


@app.get("/api/regime-lab/{aid}")
def analysis(aid: str):
    return {"analysis": DOC, "quality": rq.summarize(DOC)}


@app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def catch_all(rest: str, request: Request):
    return JSONResponse({})
