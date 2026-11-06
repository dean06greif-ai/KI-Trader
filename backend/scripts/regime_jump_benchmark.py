"""Offline-Benchmark: Detektoren (kombi/ema/reactive/jump) mit dem Autopilot-
Bewerter und Referenz v2 vergleichen. Nur lesend, keine DB.
  python scripts/regime_jump_benchmark.py /pfad/kerzen.pkl [n_random]
Die Pickle-Datei enthält {symbol: [kerzen...]} (z.B. 1h, 1080 Tage).
"""
import pickle
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import regime_autopilot as ap, research_validation  # noqa: E402

KEYS = ("inner_reference_f1_pct", "train_reference_f1_pct", "holdout_reference_f1_pct",
        "holdout_kappa_pct", "live_direction_phase_days", "reference_lag_days",
        "holdout_direction_pct")


def split(hist, train_pct=0.75):
    train, bounds, inner = {}, {}, {}
    for s, c in hist.items():
        cut = int(len(c) * train_pct)
        train[s], bounds[s] = c[:cut], int(c[cut - 1]["timestamp"])
        inner[s] = research_validation.inner_anchor_ts(c, cut)
    return train, bounds, inner


def run(hist, cands, tf="1h"):
    train, bounds, inner = split(hist)
    rows = []
    for name, cfg in cands:
        t = time.time()
        m = ap.evaluate_config(cfg, hist, train, bounds, inner, tf)
        sc = ap.score_metrics(m, 4, 14) if m else None
        rows.append((sc if sc is not None else -999, name, m, cfg))
        vals = " ".join(f"{k.replace('_pct', '').replace('reference_', 'ref_')}="
                        f"{(m or {}).get(k)}" for k in KEYS)
        print(f"{name:<34} score={sc} {vals} ({time.time() - t:.1f}s)", flush=True)
    return rows


def main():
    hist = pickle.load(open(sys.argv[1], "rb"))
    n_rand = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    base = {"regime_mode": 3}
    cands = [
        ("kombi default", dict(base, detector="kombi")),
        ("kombi ema8 dom5 (Mini-Suche 24.09.)", dict(base, detector="kombi", kombi_ema_days=8.0,
                                                    kombi_dominance_days=5.0)),
        ("ema thr0.34 smooth0.75", dict(base, detector="ema", ema_regime_thr=0.34,
                                        ema_regime_smooth_days=0.75)),
        ("reactive default", dict(base, detector="reactive")),
        ("jump default", dict(base, detector="jump")),
    ]
    rng = random.Random(11)
    for _ in range(n_rand):
        cfg = dict(base, detector="jump")
        for k, spec in ap.DETECTOR_SPACE.get("jump", {}).items():
            cfg[k] = ap._sample(spec, rng)
        cands.append((f"jump {cfg.get('jump_fast_days')}/{cfg.get('jump_slow_days')}/"
                      f"{cfg.get('jump_center')}/{cfg.get('jump_penalty_days')}", cfg))
    rows = run(hist, cands)
    rows.sort(key=lambda r: -r[0])
    print("\nTOP 8:")
    for sc, name, m, _cfg in rows[:8]:
        print(f"  {sc:.1f} {name}: F1 hold {m.get('holdout_reference_f1_pct')} κ {m.get('holdout_kappa_pct')} "
              f"phase {m.get('live_direction_phase_days')}")


if __name__ == "__main__":
    main()
