"""Repro Autopilot-Basisbewertung auf ECHTEN Cache-Kerzen (CANDLE_CACHE_DIR)."""
import asyncio
import json
import sys

sys.path.insert(0, "/app/backend")
from services import regime_autopilot as ap, regime_lab as lab, research_validation  # noqa: E402
from services import regime as rg  # noqa: E402


async def main(sym, tf, days, cfg):
    hist = await lab.fetch_histories([sym], days, tf)
    if not hist:
        print("KEINE HISTORIE (<=100 Bars)")
        return
    n = len(hist[sym])
    cut = min(max(int(n * 0.75), 100), n)
    train = {sym: hist[sym][:cut]}
    bounds = {sym: int(hist[sym][cut - 1]["timestamp"]) if cut < n else None}
    anchor = {sym: research_validation.inner_anchor_ts(hist[sym], cut)}
    cfg = dict(cfg)
    cfg["detector"] = ap.detector_of(cfg)
    print(f"{sym} {tf}: bars={n} train={cut} detector={cfg['detector']}")
    model = rg.detect_regimes(train, tf, 5, 3.0, 5.0, engine="v2", engine_config=cfg)
    print("model:", "OK" if model else "NONE")
    m = ap.evaluate_config(cfg, hist, train, bounds, anchor, tf)
    print("metrics:", m)


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "OIL"
    tf = sys.argv[2] if len(sys.argv) > 2 else "24h"
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 1080
    cfg = json.loads(sys.argv[4]) if len(sys.argv) > 4 else {"version": "v2", "regime_mode": 5}
    asyncio.run(main(sym, tf, days, cfg))
