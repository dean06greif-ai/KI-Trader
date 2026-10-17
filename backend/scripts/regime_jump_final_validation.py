"""Offline-Validierung der Jump-Rückblick-Sicht (jump_final_center_ratio).
Nur lesend, keine DB. Zwei unabhängige Prüfungen:
  A) Synthetische Serien mit BEKANNTER Wahrheit (auf/seit/ab je Abschnitt)
  B) Echte Kerzen (Pickle {symbol: [kerzen]}): Referenz v2 + eingebaute
     Plausibilitätsprüfung (validate_labels) – ohne Parameter-Anpassung.
  python scripts/regime_jump_final_validation.py synth
  python scripts/regime_jump_final_validation.py real /pfad/kerzen.pkl [tf]
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import regime_engine as eng  # noqa: E402
from services import regime_reactive as rx  # noqa: E402
from services import regime_reference, regime_truth as rt  # noqa: E402

RATIOS = (1.0, 0.8, 0.65, 0.5)


def macro_f1(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    m = (a >= 0) & (b >= 0)
    a, b = a[m], b[m]
    return float(np.mean([2 * np.sum((a == k) & (b == k)) / max(np.sum(a == k) + np.sum(b == k), 1)
                          for k in (0, 1, 2)]) * 100)


def labels(candles, tf, **extra):
    cfg = eng.resolve_config({"regime_mode": 3, "detector": "jump", **extra}, tf, len(candles))
    f = eng.compute_matrix(candles, cfg)
    live, _c, det = rx.classify(f, cfg)
    return cfg, np.asarray(live), np.asarray(rx.final_ids_from(det, f, cfg))


def synth_series(rng, days=360, bpd=24):
    """Abschnitte 5-40 Tage; Trend-Drift 0,1-0,4 x Tagesvola je Tag; Seitwärts =
    mean-reverting Range. Rückgabe (kerzen, wahrheit je Kerze)."""
    dvol = rng.uniform(1.5, 4.0) / 100.0
    sig = dvol / np.sqrt(bpd)
    r, truth, x = [], [], 0.0
    while len(r) < days * bpd:
        k = int(rng.integers(0, 3))
        n = int(rng.uniform(5, 40) * bpd)
        mu = rng.uniform(0.1, 0.4) * dvol / bpd * (k - 1)
        for _ in range(n):
            if k == 1:
                step = -0.02 * x + rng.normal(0, sig)
                x += step
                r.append(step)
            else:
                x = 0.0
                r.append(mu + rng.normal(0, sig))
            truth.append(k)
    close = 100 * np.exp(np.cumsum(r[:days * bpd]))
    out = []
    for i, c in enumerate(close):
        o = close[i - 1] if i else c
        out.append({"timestamp": 1_700_000_000_000 + i * 3_600_000, "open": float(o),
                    "high": float(max(o, c) * (1 + sig / 2)), "low": float(min(o, c) * (1 - sig / 2)),
                    "close": float(c), "volume": 1.0})
    return out, np.asarray(truth[:days * bpd])


def run_synth(n_series=24):
    rng = np.random.default_rng(2026)
    res = {r: {"fin": [], "live": [], "side": []} for r in RATIOS}
    true_side = []
    for _ in range(n_series):
        c, truth = synth_series(rng)
        true_side.append(np.mean(truth == 1))
        for ratio in RATIOS:
            cfg, live, fin = labels(c, "1h", jump_final_center_ratio=ratio)
            w = int(cfg.get("warmup_bars") or 0)
            res[ratio]["fin"].append(macro_f1(fin[w:], truth[w:]))
            res[ratio]["live"].append(macro_f1(live[w:], truth[w:]))
            res[ratio]["side"].append(np.mean(fin[fin >= 0] == 1) * 100)
    print(f"Synthetisch {n_series} Serien, wahre Seitwärts-Quote {np.mean(true_side) * 100:.0f} %")
    for ratio in RATIOS:
        v = res[ratio]
        print(f"  ratio {ratio:<4}  Final-F1 vs Wahrheit {np.mean(v['fin']):5.1f} (min {np.min(v['fin']):4.1f})"
              f"  Live-F1 {np.mean(v['live']):5.1f}  Seitwärts {np.mean(v['side']):4.0f} %")


def run_real(path, tf="1h"):
    import pickle
    hist = pickle.load(open(path, "rb"))
    for sym, c in hist.items():
        row = []
        for ratio in RATIOS:
            cfg, live, fin = labels(c, tf, jump_final_center_ratio=ratio)
            ref = [-1 if v is None else v for v in
                   rt.centered_labels(c, regime_reference.reference_cfg(cfg), 3)]
            val = eng.validate_labels(c, [None if v < 0 else int(v) for v in fin], {"config": cfg})
            row.append(f"{ratio}: F1 {macro_f1(fin, ref):4.1f} Verstöße {val['violation_bars_pct']:4.1f}% "
                       f"Seitw {np.mean(fin[fin >= 0] == 1) * 100:3.0f}%")
        refside = np.mean(np.asarray(ref)[np.asarray(ref) >= 0] == 1) * 100
        print(f"{sym:<9} (Ref seitw {refside:3.0f}%) | " + " | ".join(row), flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "synth":
        run_synth()
    else:
        run_real(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "1h")
