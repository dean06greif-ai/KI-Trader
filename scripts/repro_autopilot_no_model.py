"""Repro: Autopilot 'Ausgangs-Konfiguration liefert kein Modell' bei wenigen Tageskerzen."""
import random
import sys
import time

sys.path.insert(0, "/app/backend")
from services import regime_autopilot as ap  # noqa: E402
from services import research_validation  # noqa: E402


def daily(n, seed=1):
    rng = random.Random(seed)
    ts0 = int(time.time() * 1000) - n * 86400000
    p, out = 70.0, []
    for i in range(n):
        o = p
        p = max(1.0, p * (1 + rng.gauss(0.0005, 0.02)))
        hi, lo = max(o, p) * (1 + abs(rng.gauss(0, 0.005))), min(o, p) * (1 - abs(rng.gauss(0, 0.005)))
        out.append({"timestamp": ts0 + i * 86400000, "open": o, "high": hi, "low": lo,
                    "close": p, "volume": 1000 + rng.random() * 500})
    return out


def run(n, detector, tf="24h", train_pct=75):
    hist = {"OIL": daily(n)}
    cut = min(max(int(n * train_pct / 100.0), 100), n)
    train = {"OIL": hist["OIL"][:cut]}
    bounds = {"OIL": int(hist["OIL"][cut - 1]["timestamp"]) if cut < n else None}
    anchor = {"OIL": research_validation.inner_anchor_ts(hist["OIL"], cut)}
    cfg = {"version": "v2", "detector": detector}
    cfg["detector"] = ap.detector_of(cfg)
    m = ap.evaluate_config(cfg, hist, train, bounds, anchor, tf)
    return m


if __name__ == "__main__":
    for n in (int(a) for a in (sys.argv[1:] or ["250", "365", "1000"])):
        for det in ("ema", "reactive", "kombi"):
            m = run(n, det)
            print(n, det, "OK" if m else "NONE", (m or {}).get("holdout_direction_pct"))
