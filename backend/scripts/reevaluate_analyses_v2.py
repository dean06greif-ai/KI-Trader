"""Nur LESEND: alle gespeicherten Regime-Analysen mit Referenz v2 nachmessen.
Lädt die Kerzen exakt wie das Regime-Lab (gleiches Datenfenster aus `bounds`)
und bewertet das gespeicherte kombinierte Modell. Schreibt NICHTS.
  PROD_MONGO_URL=... python scripts/reevaluate_analyses_v2.py [aid ...] [--max-symbols 5]
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient  # noqa: E402
from services import regime_lab as lab, candle_archive  # noqa: E402


async def _no_upload(*_a, **_k):
    return False

candle_archive.upload = _no_upload  # strikt lesend: nie ins Supabase-Archiv schreiben


DIRECT = "--direct" in sys.argv


def _mean(v):
    v = [x for x in v if x is not None]
    return round(sum(v) / len(v), 1) if v else None


async def evaluate(doc, max_symbols):
    model = doc["combined"]["model"]
    cfg = model["config"]
    tf = doc["timeframe"]
    syms = doc["symbols"][:max_symbols]
    b = doc.get("bounds") or {}
    if DIRECT and tf in ("15m", "1h", "4h"):
        # Schnellweg Krypto: 1h-Kerzen direkt von Bitunix (statt 1080 d 1m-Kerzen
        # nachzuladen) – gleiche Daten, auf das Analyse-Fenster geschnitten.
        from scripts.reference_window_probe import load_1h
        from core import instruments
        hist = {}
        for s in syms:
            inst = instruments.get(s)
            bx = (getattr(inst, "trade_symbol", None) or getattr(inst, "bitunix_symbol", None)) if inst else None
            bx = bx or {"GOLD": "XAUUSDT", "SILVER": "XAGUSDT", "OIL": "CLUSDT"}.get(s, s)
            c = await load_1h(bx, int(doc["days"]) + 5, tf)
            lo, hi = (b.get(s) or {}).get("start_ts", 0), (b.get(s) or {}).get("end_ts", 1 << 62)
            hist[s] = [k for k in c if lo <= k["timestamp"] < hi]
    else:
        hist = await lab.fetch_histories(
            syms, int(doc["days"]), tf,
            start_ts={s: b[s]["start_ts"] for s in syms if s in b},
            end_ts={s: b[s]["end_ts"] for s in syms if s in b})
    rows = []
    for s, c in hist.items():
        bs = b.get(s) or {}
        _, e = lab._symbol_payload(model, c, tf, float(cfg.get("confidence_min") or 0.55),
                                   float(cfg.get("min_hold_days") or 0), False,
                                   bs.get("train_end_ts"), bs.get("inner_start_ts"))
        old = ((doc["combined"].get("per_symbol") or {}).get(s) or {})
        r2, la = e.get("reference") or {}, e.get("live_agreement") or {}
        r1 = old.get("reference") or {}
        rows.append((s, la.get("holdout_direction_pct"), r1.get("holdout_direction_pct"),
                     r2.get("holdout_direction_pct"), r2.get("holdout_balanced_pct"),
                     r2.get("holdout_baseline_pct"), r2.get("holdout_skill_pct"),
                     r2.get("mean_lag_days"), r2.get("missed_pct"),
                     r2.get("live_direction_phase_days"), r2.get("truth_phase_days"), len(c)))
    print(f"\n== {doc['id']} {doc['name']} · TF {tf} · det {cfg.get('detector')} · "
          f"Profil {cfg.get('adapt_profile')} · Horizonte {cfg.get('horizons_days')}")
    print("  Symbol | Live=Final | Ref v1 roh | Ref v2 roh | v2 balanciert | Baseline | Skill | "
          "Lag d | verpasst % | Ø Richtungs-Phase live/Ref d | Kerzen")
    for r in rows:
        print("  " + " | ".join(str(x) for x in r))
    cols = list(zip(*[r[1:11] for r in rows])) if rows else []
    if cols:
        print("  MITTEL | " + " | ".join(str(_mean(c)) for c in cols))


async def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    mx = int(sys.argv[sys.argv.index("--max-symbols") + 1]) if "--max-symbols" in sys.argv else 5
    args = [a for a in args if not a.isdigit()]
    db = MongoClient(os.environ["PROD_MONGO_URL"])["crypto_scanner"]
    q = {"id": {"$in": args}} if args else {}
    for doc in db.regime_analyses.find(q, {"_id": 0, "chart": 0, "chart_emas": 0, "per_coin": 0}):
        try:
            await evaluate(doc, mx)
        except Exception as e:  # noqa: BLE001
            print(f"\n== {doc.get('id')}: FEHLER {e}")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
