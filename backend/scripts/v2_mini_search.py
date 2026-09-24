"""Nur LESEND/offline: Mini-Suche mit dem Autopilot-Bewerter und Referenz v2.
Frage: Gibt es Detektor-Konfigurationen, die gegen die ehrliche Referenz
(balanciert, Skill, Richtungs-Phase 4–14 d) deutlich besser sind als die
gespeicherte Analyse? Lädt 1h-Kerzen direkt von Bitunix.
  PROD_MONGO_URL=... python scripts/v2_mini_search.py BTCUSDT,ETHUSDT,SOLUSDT 1080 40
"""
import asyncio
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient  # noqa: E402
from services import regime_autopilot as ap, research_validation  # noqa: E402
from scripts.reference_window_probe import load_1h  # noqa: E402


def fmt(m, k):
    v = (m or {}).get(k)
    return "–" if v is None else f"{v:.1f}"


async def main():
    syms = sys.argv[1].split(",")
    days = int(sys.argv[2])
    n = int(sys.argv[3])
    base = MongoClient(os.environ["PROD_MONGO_URL"])["crypto_scanner"].regime_analyses.find_one(
        {"id": "ra_d41ad11b"}, {"settings.engine_config": 1})["settings"]["engine_config"]
    hist = {s: await load_1h(s, days) for s in syms}
    train, bounds, inner = {}, {}, {}
    for s, c in hist.items():
        cut = int(len(c) * 0.75)
        train[s], bounds[s] = c[:cut], int(c[cut - 1]["timestamp"])
        inner[s] = research_validation.inner_anchor_ts(c, cut)
    rng = random.Random(7)
    cands = [("gespeichert (ra_d41ad11b)", dict(base))]
    for det in ("kombi", "ema", "reactive"):
        for mode in (3, 9):
            c = dict(base, detector=det, regime_mode=mode)
            cands.append((f"{det} m{mode} Start", c))
    best_k = dict(base, kombi_ema_days=8.0, kombi_dominance_days=5.0)
    cands.append(("kombi ema8 dom5 (Mini-Suche)", best_k))
    if "--htf" in sys.argv:
        cands = cands[:1] + [cands[-1]]
        for hd in (2.0, 4.0, 7.0):
            for thr in (0.2, 0.5):
                for prom in (0.0, 1.2, 2.0):
                    cands.append((f"kombi8/5 HTF {hd}d thr{thr} prom{prom}",
                                  dict(best_k, htf_confirm=True, htf_days=hd, htf_thr=thr, htf_promote_thr=prom)))
    while len(cands) < n:
        det = rng.choice(["kombi", "ema", "reactive"])
        c = ap.mutate(dict(base, detector=det, regime_mode=rng.choice([3, 9])), rng, False, rng.randint(2, 8))
        cands.append((f"{det} m{c.get('regime_mode')} zufall", c))
    rows = []
    for name, cfg in cands:
        t = time.time()
        m = await asyncio.to_thread(ap.evaluate_config, cfg, hist, train, bounds, inner, "1h")
        sc = ap.score_metrics(m, 4, 14) if m else None
        rows.append((sc or -999, name, m, cfg, round(time.time() - t)))
        print(f"{name:<28} score {sc if sc is None else round(sc, 1)} | bal innen {fmt(m, 'inner_reference_bal_pct')} "
              f"holdout {fmt(m, 'holdout_reference_bal_pct')} | F1 innen {fmt(m, 'inner_reference_f1_pct')} hold {fmt(m, 'holdout_reference_f1_pct')} κ {fmt(m, 'holdout_kappa_pct')} | "
              f"Richtungs-Phase {fmt(m, 'live_direction_phase_days')} d | LF hold {fmt(m, 'holdout_direction_pct')} "
              f"| lag {fmt(m, 'reference_lag_days')} | Nutzen 3d {fmt(m, 'utility_separation_pct')}% "
              f"Richtung bestätigt {fmt(m, 'utility_sign_hit_pct')}% | {rows[-1][4]}s", flush=True)
    rows.sort(key=lambda r: -r[0])
    print("\nTOP 5 (Score v2, Ziel 4–14 d):")
    for sc, name, m, cfg, _ in rows[:5]:
        diff = {k: v for k, v in cfg.items() if base.get(k) != v}
        print(f"  {round(sc, 1)} {name}: holdout bal {fmt(m, 'holdout_reference_bal_pct')} F1 "
              f"{fmt(m, 'holdout_reference_f1_pct')} κ {fmt(m, 'holdout_kappa_pct')} phase {fmt(m, 'live_direction_phase_days')} | {diff}")


if __name__ == "__main__":
    asyncio.run(main())
