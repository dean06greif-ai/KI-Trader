"""Grid: welche Engine-Configs liefern auf echten OIL-Tageskerzen KEIN Modell?"""
import asyncio, itertools, sys
sys.path.insert(0, "/app/backend")
from services import regime_autopilot as ap, regime_lab as lab, research_validation, regime as rg

async def main(sym, tf, days):
    hist = await lab.fetch_histories([sym], days, tf)
    n = len(hist[sym]); cut = min(max(int(n * 0.75), 100), n)
    train = {sym: hist[sym][:cut]}
    print(f"{sym} bars={n} train={cut}", flush=True)
    grid = {"detector": ["reactive", "ema", "kombi"], "regime_mode": [3, 5, 9],
            "adapt_profile": ["auto", "fein", "standard", "grob", "off"],
            "min_phase_days": [0, 10], "confidence_min": [0.5, 0.8]}
    keys = list(grid)
    bad = 0
    for vals in itertools.product(*[grid[k] for k in keys]):
        cfg = {"version": "v2", **dict(zip(keys, vals))}
        model = rg.detect_regimes(train, tf, 5, 3.0, 5.0, engine="v2", engine_config=cfg)
        if not model:
            bad += 1; print("NONE", cfg, flush=True)
    print("done, none-count:", bad, flush=True)

asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "OIL", "24h", 1080))
