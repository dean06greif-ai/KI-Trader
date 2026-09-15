"""AP05-Solltests (Befunde R06/R07/R11/R14/R16): reproduzierbare Datenstände,
halboffene Segmentgrenzen, Teilkerzen-Politik, Cache-Overlap, K-Means-Präzision."""
import asyncio
import inspect
import json

import numpy as np
import pytest

from services import regime as rg
from services import regime_lab as lab
from services import research_dataset as rd

pytestmark = pytest.mark.unit


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _candles(n=600, start=1_700_000_000_000, step=60_000, vol=5.0, seed=7):
    rng = np.random.default_rng(seed)
    px = 100 + np.cumsum(rng.normal(0, 0.3, n))
    out = []
    for i in range(n):
        o = float(px[i])
        c = float(px[i] + rng.normal(0, 0.1))
        out.append({"timestamp": start + i * step, "open": o,
                    "high": max(o, c) + 0.05, "low": min(o, c) - 0.05,
                    "close": c, "volume": vol})
    return out


class TestR16KMeansPrecision:
    def test_constant_feature_yields_positive_serialized_std(self):
        """Konstantes rel_volume: norm_std wird NICHT auf 0 gerundet;
        Klassifikation bleibt endlich (kein divide-by-zero)."""
        candles = _candles(vol=5.0)  # konstantes Volumen -> rel_volume konstant
        model = rg.detect_regimes({"BTC": candles}, "1m", 3, 0.02, engine="kmeans")
        assert model, "Modell konnte nicht gebildet werden"
        assert all(s > 0 for s in model["norm_std"]), model["norm_std"]
        # JSON-Roundtrip verändert nichts
        model2 = json.loads(json.dumps(model))
        assert model2["norm_std"] == model["norm_std"]
        feats = rg.compute_features(candles, model["lookback_bars"])
        with np.errstate(divide="raise", invalid="raise"):
            rid, conf, valid = rg.classify_matrix(model2, feats)
        assert valid.any()
        assert np.isfinite(conf[valid]).all()

    def test_legacy_model_with_zero_std_is_floored(self):
        """Altmodelle mit gespeicherter std=0.0 (6-Dezimal-Rundung) dürfen
        keine NaN/Inf mehr erzeugen."""
        model = {"norm_mean": [0.0, 0.0, 0.5, 1.0],
                 "norm_std": [1.0, 1.0, 0.2, 0.0],  # letzte Dimension kaputt
                 "centroids": [[0.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0]]}
        with np.errstate(divide="raise", invalid="raise"):
            rid, conf, sims = rg.classify_point(model, [0.1, 0.2, 0.6, 1.0])
        assert rid in (0, 1) and np.isfinite(conf)
        feats = np.array([[0.1, 0.2, 0.6, 1.0], [0.5, 0.1, 0.4, 1.0]])
        with np.errstate(divide="raise", invalid="raise"):
            rid2, conf2, valid = rg.classify_matrix(model, feats)
        assert valid.all() and np.isfinite(conf2).all()


class TestR07HalfOpenSegments:
    def _labeled(self, n=60):
        candles = _candles(n)
        labels = [0] * 30 + [1] * 30
        return candles, labels

    def test_segments_payload_uses_last_included_candle(self):
        candles, labels = self._labeled()
        segs = lab._segments_payload(candles, labels)
        assert len(segs) == 2
        a, b = segs
        # to_ts = letzte Kerze des Segments, nicht die erste der Folgephase
        assert a["to_ts"] == candles[29]["timestamp"]
        assert a["end_exclusive_ts"] == candles[30]["timestamp"] == b["from_ts"]
        assert "end_exclusive_ts" not in b  # letztes Segment endet mit den Daten

    def test_reader_counts_every_candle_exactly_once(self):
        candles, labels = self._labeled()
        segs = lab._segments_payload(candles, labels)
        seen = []
        for s in segs:
            r = {"from_ts": s["from_ts"], "to_ts": s["to_ts"]}
            if "end_exclusive_ts" in s:
                r["end_exclusive_ts"] = s["end_exclusive_ts"]
            mapped = lab.segments_from_ranges(candles, [r], s["regime"],
                                              warmup_bars=0)
            assert mapped and mapped[0]["n_bars"] == s["bars"]
            seen += [c["timestamp"] for c in mapped[0]["candles"]]
        assert len(seen) == len(set(seen)) == len(candles), \
            "Segmentgrenze zählt eine Kerze doppelt oder lässt eine aus"

    def test_legacy_ranges_without_end_exclusive_keep_old_reader(self):
        candles, labels = self._labeled()
        # Altdokument: to_ts zeigt (fehlerhaft) auf die erste Kerze der Folgephase
        legacy = {"from_ts": candles[0]["timestamp"],
                  "to_ts": candles[30]["timestamp"]}
        mapped = lab.segments_from_ranges(candles, [legacy], 0, warmup_bars=0)
        assert mapped[0]["n_bars"] == 31  # dokumentiertes Legacy-Verhalten


class TestR06DatasetManifest:
    def test_manifest_roundtrip_and_verify_ok(self):
        h = {"BTC": _candles(120)}
        m = rd.dataset_manifest(h, "1m")
        assert m["per_symbol"]["BTC"]["bars"] == 120
        assert rd.verify_histories(h, m) == []
        # JSON-Roundtrip (so liegt es in Mongo) bleibt prüfbar
        assert rd.verify_histories(h, json.loads(json.dumps(m))) == []

    def test_changed_history_is_reported(self):
        h = {"BTC": _candles(120)}
        m = rd.dataset_manifest(h, "1m")
        h2 = {"BTC": [dict(c) for c in h["BTC"]]}
        h2["BTC"][50]["close"] += 0.5
        problems = rd.verify_histories(h2, m)
        assert problems and "Checksum" in problems[0]
        h3 = {"BTC": h["BTC"][:100]}
        problems = rd.verify_histories(h3, m)
        assert problems and "Kerzenanzahl" in problems[0]
        assert rd.verify_histories({}, m)[0].startswith("BTC: Daten fehlen")

    def test_dataset_status_legacy_vs_pinned(self):
        assert rd.dataset_status({}) == "legacy_unpinned"
        assert rd.dataset_status({"dataset": {"per_symbol": {"BTC": {}}}}) == "pinned"

    def test_fetch_histories_contract_uses_anchors_and_manifest(self):
        """Struktureller Nachweis: fetch_histories unterstützt start_ts/dataset,
        aggregiert ohne Teilkerzen und bricht bei Manifest-Abweichung ab."""
        src = inspect.getsource(lab.fetch_histories)
        assert "drop_partial=True" in src
        assert "verify_histories" in src and "RuntimeError" in src
        sig = inspect.signature(lab.fetch_histories)
        assert "start_ts" in sig.parameters and "dataset" in sig.parameters


class TestR11PartialCandlePolicy:
    def test_aggregate_drops_partial_bucket(self):
        from services.timeframes import aggregate_candles
        candles = _candles(65)  # 65 Minuten -> 2. Stunden-Bucket unvollständig
        full = aggregate_candles(candles, "1h", drop_partial=False)
        closed = aggregate_candles(candles, "1h", drop_partial=True)
        assert len(full) == 2 and len(closed) == 1
        assert closed[0]["timestamp"] == full[0]["timestamp"]

    def test_regime_paths_use_closed_buckets_only(self):
        import services.dynamic_live as dl
        import services.regime_gate as gate
        assert inspect.getsource(dl.detect_current).count("drop_partial=True") == 1
        assert "drop_partial=True" in inspect.getsource(dl.refresh_state)
        assert "drop_partial=True" in inspect.getsource(gate._detect)


class TestR14CacheOverlapReload:
    def test_merge_tail_replaces_frozen_partial_candle(self):
        from services.candle_cache import _merge_tail
        from services.candles import CandleArray
        old = CandleArray.from_dicts(_candles(10))
        updated = _candles(12)
        updated[9]["close"] += 3.0  # letzte gecachte Kerze war noch offen
        tail = CandleArray.from_dicts(updated[8:])  # überlappendes Nachladen
        merged = _merge_tail(old, tail)
        assert len(merged) == 12
        ts = merged.ts.tolist()
        assert len(ts) == len(set(ts)), "Duplikate nach Merge"
        i = ts.index(updated[9]["timestamp"])
        assert float(merged.cl[i]) == pytest.approx(updated[9]["close"])

    def test_get_candles_refetches_with_overlap(self, monkeypatch):
        import time as _t

        import services.candle_cache as cc
        from services.candles import CandleArray
        now_min = (int(_t.time() * 1000) // 60000) * 60000
        start0 = now_min - 12 * 60000
        base = _candles(10, start=start0)
        cc._MEM.clear()
        cc._MEM["TESTUSDT"] = {"candles": CandleArray.from_dicts(base),
                               "last_refresh": 0, "used_at": 0}
        calls = {}

        async def fake_fetch(session, symbol, start_ms, end_ms, job=None, pace=None):
            calls["start"] = start_ms
            updated = _candles(12, start=start0)
            updated[9]["close"] += 3.0
            keep = [c for c in updated if c["timestamp"] >= start_ms]
            return CandleArray.from_dicts(keep)

        monkeypatch.setattr(cc, "_fetch_range", fake_fetch)
        monkeypatch.setattr(cc, "DISK_ENABLED", False)
        from core import instruments
        monkeypatch.setattr(instruments, "history_days_cap",
                            lambda sym, days: days, raising=False)
        out = _run(cc.get_candles(None, "TESTUSDT", days=1))
        last_cached_ts = base[-1]["timestamp"]
        assert calls["start"] <= last_cached_ts, \
            "Tail-Reload muss die letzte gecachte Kerze erneut anfordern (Overlap)"
        ts = out.ts.tolist()
        i = ts.index(last_cached_ts)
        assert float(out.cl[i]) == pytest.approx(base[-1]["close"] + 3.0)
        assert len(ts) == len(set(ts))
        cc._MEM.clear()
