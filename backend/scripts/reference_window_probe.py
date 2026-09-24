"""Nur LESEND/offline: Wie hängt die Referenz (zentrierte Rückblick-Phasen) vom
Fenster ab? Lädt öffentliche Bitunix-1h-Kerzen und zeigt je Fenster die Ø
Referenz-Phasendauer und den Anteil Trend/Seitwärts. Aufruf:
  python scripts/reference_window_probe.py BTCUSDT 810
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.bitunix_client import fetch_klines_range  # noqa: E402
from services import regime_truth as rt, regime_engine as eng  # noqa: E402


async def load_1h(sym: str, days: int):
    end = int(time.time() * 1000)
    start = end - days * 86400000
    out, cur = {}, start
    while cur < end:
        ks = await fetch_klines_range(sym, "1h", cur, min(cur + 200 * 3600000, end), limit=200)
        if not ks:
            cur += 200 * 3600000
            continue
        for k in ks:
            out[k["time"]] = k
        cur = max(k["time"] for k in ks) * 1000 + 3600000
    return [{"timestamp": t * 1000, **{x: v[x] for x in ("open", "high", "low", "close")},
             "volume": 0.0} for t, v in sorted(out.items())]


def stats(labels, bpd, mode=3):
    tr = [None if l is None else eng.split_id(int(l), mode)[0] for l in labels]
    segs, cur, n = [], None, 0
    for t in tr:
        if t is None:
            continue
        if t != cur and n:
            segs.append((cur, n))
            n = 0
        cur = t
        n += 1
    if n:
        segs.append((cur, n))
    days = [s[1] / bpd for s in segs]
    share = {k: round(sum(s[1] for s in segs if s[0] == k) / max(sum(s[1] for s in segs), 1) * 100)
             for k in (0, 1, 2)}
    return len(segs), round(sum(days) / max(len(days), 1), 1), share


async def main():
    sym = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 810
    c = await load_1h(sym, days)
    print(f"{sym}: {len(c)} 1h-Kerzen")
    base = {"bars_per_day": 24.0, "regime_mode": 3}
    for w in (5, 7, 10, 14, 20, 40):
        for mn in (None, 2.0):
            cfg = {**base, "horizons_days": [w]}
            if mn:
                cfg.update({"reference_window_days": w, "reference_min_days": mn})
            n, avg, share = stats(rt.centered_labels(c, cfg, 3), 24.0)
            print(f"W={w:>3}d merge={'alt (0.8% Zeitraum)' if not mn else f'{mn}d':>18}: "
                  f"{n:>4} Phasen, Ø {avg:>5} d, Anteil ab/seit/auf {share}")

if __name__ == "__main__":
    asyncio.run(main())
