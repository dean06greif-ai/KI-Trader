"""Regression R06: Regime-Lab lädt nur laut Wanduhr GESCHLOSSENE Buckets.

Vorher blieb der letzte Bucket erhalten, sobald seine letzte 1m-Kerze
existierte (noch in Bildung). Minuten später änderte sich Close/Volumen, die
Manifest-Checksum passte nicht mehr und die Regime-Suche brach direkt nach
einer frischen Analyse mit "Datensatz nicht reproduzierbar" ab.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))

from services import regime_lab as lab  # noqa: E402
from services import research_dataset as rd  # noqa: E402


def _minute_candles(n, end_ms, vol=1.0):
    start = end_ms - (n - 1) * 60000
    out = []
    for i in range(n):
        ts = start + i * 60000
        p = 100.0 + (ts // 60000) % 500  # Preis hängt nur vom Zeitstempel ab
        out.append({"timestamp": ts, "open": p, "high": p + 1, "low": p - 1,
                    "close": p + 0.5, "volume": vol})
    return out


def _run(monkeypatch, candles):
    async def fake_fetch(session, sym, days, job=None):
        return list(candles)
    import services.backtester as bt
    monkeypatch.setattr(bt, "fetch_history", fake_fetch)
    return asyncio.run(lab.fetch_histories(["BTCUSDT"], 3, "15m", None))["BTCUSDT"]


def test_forming_bucket_is_dropped_and_manifest_stable(monkeypatch):
    now_ms = int(time.time() * 1000)
    cur_min = now_ms - now_ms % 60000  # laufende 1m-Kerze
    # 2400 Minuten bis inkl. der laufenden Kerze -> letzter 15m-Bucket ist offen
    c1 = _minute_candles(2400, cur_min, vol=1.0)
    h1 = _run(monkeypatch, c1)
    bucket_end_last = h1[-1]["timestamp"] + 15 * 60000
    assert bucket_end_last <= now_ms, "letzter Bucket muss komplett geschlossen sein"
    # Zweiter Abruf: die laufende Kerze hat sich verändert (Volumen/Close)
    c2 = [dict(c) for c in c1]
    c2[-1]["volume"] = 999.0
    c2[-1]["close"] = 555.0
    h2 = _run(monkeypatch, c2)
    assert rd.symbol_manifest(h1) == rd.symbol_manifest(h2)
    assert not rd.verify_histories({"BTCUSDT": h2}, rd.dataset_manifest({"BTCUSDT": h1}, "15m"))


def test_closed_buckets_are_kept(monkeypatch):
    now_ms = int(time.time() * 1000)
    # Historie endet vor 30 Minuten -> alle Buckets geschlossen, nichts verworfen
    end = now_ms - now_ms % 900000 - 60000  # letzte Minute eines geschlossenen 15m-Buckets
    c = _minute_candles(2400, end)
    h = _run(monkeypatch, c)
    assert h[-1]["timestamp"] == end - 14 * 60000
    # 2400 Minuten = 160 volle Buckets, Anfang liegt exakt auf dem Raster -> alle da
    assert len(h) == 160


def test_partial_first_bucket_is_dropped(monkeypatch):
    """Historie beginnt mitten im Bucket (Tage ab JETZT, nicht am Raster):
    der erste, nur teilweise gefüllte Bucket darf nicht ins Manifest."""
    now_ms = int(time.time() * 1000)
    end = now_ms - now_ms % 900000 - 60000
    c = _minute_candles(2400 + 7, end)  # 7 zusätzliche Minuten = angeschnittener Bucket
    h = _run(monkeypatch, c)
    assert h[0]["timestamp"] >= c[0]["timestamp"]
    assert len(h) == 160
    # Mit mehr Kopf-Historie (Nachladen) bleibt das Fenster identisch
    c_more = _minute_candles(2400 + 7 + 60, end)
    h_more = _run(monkeypatch, c_more)
    h_more = [x for x in h_more if x["timestamp"] >= h[0]["timestamp"]]
    assert rd.symbol_manifest(h_more) == rd.symbol_manifest(h)
