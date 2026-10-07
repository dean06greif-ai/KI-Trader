"""Diagnose: Ø Phasendauer je Regime-Anzahl (3/5/9) bei IDENTISCHER Erkennung.

Prüft die Frage „warum erreichen 3/5 Regime den Sweet Spot (Ø Phase ≥ 5 d)
schlechter als 9 Regime?“ auf dem Testbett (BTC/ETH 1h, scripts/_testbed_candles.pkl).
Aufruf: python scripts/regime_mode_phase_probe.py [detector ...]
"""
import os
import pickle
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import regime_autopilot as ap  # noqa: E402
from services import regime_quality as rq  # noqa: E402

DATA = os.path.join(os.path.dirname(__file__), "_testbed_candles.pkl")


def main(detectors):
    hist = pickle.load(open(DATA, "rb"))
    ctx = ap._split_train(hist, 70.0)
    lo, hi = rq.SWEET_SPOT_DAYS
    print(f"{'det':9} {'mode':>4} {'profile':>9} {'dir_phase':>9} {'reg_phase':>9} "
          f"{'penalty':>7} {'score':>7} {'F1':>6}")
    for det in detectors:
        for mode in (3, 5, 9):
            cfg = {"detector": det, "regime_mode": mode, "auto_adapt": True, "adapt_profile": "auto"}
            m = ap.evaluate_config(cfg, ctx["histories"], ctx["train_hist"], ctx["bounds"],
                                   ctx["inner_anchor"], "1h")
            if not m:
                print(det, mode, "kein Modell")
                continue
            bd = ap.score_breakdown(m, lo, hi) or {}
            print(f"{det:9} {mode:>4} {str(m.get('_profile', '-')):>9} "
                  f"{m.get('live_direction_phase_days')!s:>9} {m.get('avg_live_phase_days')!s:>9} "
                  f"{bd.get('phase_penalty')!s:>7} {bd.get('total')!s:>7} "
                  f"{m.get('train_reference_f1_pct')!s:>6}")


if __name__ == "__main__":
    main(sys.argv[1:] or ["reactive", "ema", "kombi", "jump"])
