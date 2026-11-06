"""Autopilot: Ausgangslage ohne Modell -> Standard-Feinwerte als Start + erklärende Fehlermeldung."""
import asyncio
import random
import time

from services import regime_autopilot as ap
from services import research_validation


def _daily(n, seed=1):
    rng = random.Random(seed)
    ts0 = int(time.time() * 1000) - n * 86400000
    p, out = 70.0, []
    for i in range(n):
        o = p
        p = max(1.0, p * (1 + rng.gauss(0.0005, 0.02)))
        out.append({"timestamp": ts0 + i * 86400000, "open": o, "high": max(o, p) * 1.003,
                    "low": min(o, p) * 0.997, "close": p, "volume": 1000.0})
    return out


def test_fallback_start_config_keeps_only_skeleton():
    cfg = {"version": "v2", "detector": "ema", "regime_mode": 5, "ema_regime_days": 30.0,
           "auto_adapt": False, "adapt_profile": "off", "horizons_days": [50, 100, 200]}
    fb = ap.fallback_start_config(cfg)
    assert fb == {"detector": "ema", "auto_adapt": True, "adapt_profile": "auto",
                  "regime_mode": 5, "version": "v2"}
    assert ap.fallback_start_config({}) == {"detector": "reactive", "auto_adapt": True,
                                            "adapt_profile": "auto"}


def test_no_model_reason_names_bars_and_tip():
    hist = {"OIL": _daily(120)}
    train = {"OIL": hist["OIL"][:60]}
    cfg = {"detector": "reactive", "auto_adapt": False, "horizons_days": [50, 100, 200]}
    msg = ap.no_model_reason(hist, train, "24h", cfg)
    assert msg.startswith("Ausgangs-Konfiguration liefert kein Modell")
    assert "OIL: 120 Kerzen (24h), Training 60, nötig ≥" in msg
    assert "kleineren Timeframe" in msg
    assert len(msg) <= 300


def test_no_model_reason_flags_flat_candles():
    hist = {"QQQUSDT": _daily(150)}
    for c in hist["QQQUSDT"][:40]:
        c["high"] = c["low"] = c["close"] = c["open"]
    train = {"QQQUSDT": hist["QQQUSDT"][:112]}
    msg = ap.no_model_reason(hist, train, "24h", {"detector": "ema"})
    assert "40 ohne Bewegung" in msg
    assert "Kerzen-Cache" in msg


def test_evaluate_config_daily_oil_like_history_has_model():
    """OIL/QQQ: Bitunix-Historie erst ab Listing (~180 Tageskerzen) muss reichen."""
    n = 180
    hist = {"OIL": _daily(n)}
    cut = min(max(int(n * 0.75), 100), n)
    train = {"OIL": hist["OIL"][:cut]}
    bounds = {"OIL": int(hist["OIL"][cut - 1]["timestamp"])}
    anchor = {"OIL": research_validation.inner_anchor_ts(hist["OIL"], cut)}
    for det in ("reactive", "ema", "kombi"):
        m = ap.evaluate_config({"version": "v2", "detector": det, "regime_mode": 5},
                               hist, train, bounds, anchor, "24h")
        assert m is not None, det
    # Feinwerte, die für so wenige Bars zu lang sind -> kein Modell, Fallback greift
    too_long = {"version": "v2", "detector": "reactive", "auto_adapt": False,
                "adapt_profile": "off", "horizons_days": [60, 120, 130]}
    fb = ap.fallback_start_config(too_long)
    assert ap.evaluate_config(fb, hist, train, bounds, anchor, "24h") is not None


def test_run_autopilot_uses_fallback_when_start_config_has_no_model(monkeypatch):
    from services import regime_lab as lab

    n = 180
    hist = {"OIL": _daily(n)}

    async def fake_fetch(symbols, days, timeframe, job=None, **kw):
        return hist

    monkeypatch.setattr(lab, "fetch_histories", fake_fetch)
    calls = {"n": 0}
    real_eval = ap.evaluate_config

    def eval_first_none(cfg, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_eval(cfg, *a, **kw)

    monkeypatch.setattr(ap, "evaluate_config", eval_first_none)
    job_id = lab.create_job("autopilot", {})
    body = {"symbols": ["OIL"], "timeframe": "24h", "days": 1080, "train_pct": 75,
            "max_rounds": 1, "engine_config": {"version": "v2", "detector": "ema",
                                               "ema_regime_days": 30.0}}
    asyncio.run(ap.run_autopilot(job_id, body, None))
    job = lab.JOBS[job_id]
    assert job["status"] == "done", job.get("error")
    assert job.get("start_fallback")
    assert job["result"]["start_fallback"]
    assert job["result"]["best"]["detector"] == "ema"
