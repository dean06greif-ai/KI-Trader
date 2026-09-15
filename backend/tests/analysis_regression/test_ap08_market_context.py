"""AP08-Solltests (Befund R15): Gemeinsamer MarketContext-Vertrag.

- Richtungs-Identität aus Regime-IDs (split_id), nicht aus Label-Substrings.
- Legacy-KMeans-Labels nur über den EXPLIZIT benannten Adapter.
- Observer-Kurzfrist-Regime = eigene Ebene (setup_context), keine strukturelle
  Identität.
- unknown/stale sind echte Zustände; Konfidenz ist als heuristisch deklariert.
- Gleicher Artifact + gleiche Kerzen -> Lab und Gate liefern dieselbe Richtung.
"""
import inspect

import numpy as np
import pytest

from services import market_context as mc
from services import regime_gate as gate
from services import regime_engine as eng

pytestmark = pytest.mark.unit


def _candles(n=600, start=1_700_000_000_000, step=3_600_000, seed=3, drift=0.15):
    rng = np.random.default_rng(seed)
    px = 100 + np.cumsum(rng.normal(drift, 0.3, n))
    out = []
    for i in range(n):
        o, c = float(px[i]), float(px[i] + rng.normal(0, 0.1))
        out.append({"timestamp": start + i * step, "open": o,
                    "high": max(o, c) + 0.05, "low": min(o, c) - 0.05,
                    "close": c, "volume": 5.0})
    return out


class TestDirectionContract:
    def test_direction_from_regime_id_all_modes(self):
        for mode in (3, 5, 9):
            for t in (0, 1, 2):
                rid = eng.regime_id(t, 1 if mode != 5 else 0, mode)
                assert mc.direction_from_regime_id(rid, mode) == \
                    ("down", "sideways", "up")[t], (mode, t)
        assert mc.direction_from_regime_id(None, 9) is None

    def test_legacy_label_adapter_is_named_and_bounded(self):
        assert mc.legacy_phase_from_label("Leicht aufwärts · ruhig") == "bulle"
        assert mc.legacy_phase_from_label("Abwärtstrend · hohe Volatilität") == "bär"
        assert mc.legacy_phase_from_label("Seitwärtsmarkt") == "seitwärts"
        assert mc.legacy_phase_from_label("Unbekanntes Cluster 3") is None
        assert mc.legacy_phase_from_label(None) is None
        # Gate-Funktion delegiert an den Adapter (keine eigene Substring-Kopie)
        assert "legacy_phase_from_label" in inspect.getsource(gate.phase_from_label)

    def test_observer_context_is_separate_layer(self):
        ctx = mc.observer_context("trend_up")
        assert ctx["layer"] == "setup_context"
        assert ctx["context_state"] == "short_term_up"
        assert ctx["context_state"] not in mc.DIRECTIONS  # keine Identität
        assert mc.observer_context("weird")["state"] == "unknown"

    def test_observer_features_carry_layer(self):
        from services import ai_market_observer as ob
        candles = [{"timestamp": i, "open": 100.0, "high": 100.1, "low": 99.9,
                    "close": 100.0, "volume": 1.0} for i in range(300)]
        feats = ob.compute_features(candles)
        assert feats["context_layer"] == "setup_context"
        assert feats["taxonomy_version"] == 1


class TestContextStates:
    def test_unknown_stale_ok(self):
        assert mc.context_state(None, 0, 900) == "unknown"
        assert mc.context_state("up", 1000, 900) == "stale"
        assert mc.context_state("up", 100, 900) == "ok"
        ctx = mc.structural_context("regime_gate", direction=None, label="X")
        assert ctx["state"] == "unknown" and ctx["phase"] is None
        assert ctx["confidence_kind"] == "heuristic"

    def test_model_fingerprint_stable_and_sensitive(self):
        m1 = {"engine": "kmeans", "norm_mean": [0.1, 0.2], "norm_std": [1.0, 1.0],
              "centroids": [[0, 0], [1, 1]]}
        m2 = {**m1, "centroids": [[0, 0], [1, 2]]}
        assert mc.model_fingerprint(m1) == mc.model_fingerprint(dict(m1))
        assert mc.model_fingerprint(m1) != mc.model_fingerprint(m2)
        assert mc.model_fingerprint(None) is None

    def test_gate_failure_marks_cached_entry_stale(self):
        src = inspect.getsource(gate.check_signal_allowed)
        assert '"stale"' in src  # abgelaufener Stand wird sichtbar gemacht


class TestSharedIdentityLabVsGate:
    def test_same_artifact_same_direction_in_lab_and_gate_path(self):
        """v2-Modell: Richtung aus der ID (Gate-Pfad) == Richtung aus dem
        letzten kausalen Label (Lab-Pfad) – ein Vertrag, zwei Konsumenten."""
        from services import regime as rg
        candles = _candles()
        model = rg.detect_regimes({"BTC": candles}, "1h", 5, 3.0, 5.0,
                                  engine="v2")
        assert model and rg.is_v2(model)
        mode = (model.get("config") or {}).get("regime_mode")
        cur = rg.current_regime(model, candles, "1h")
        gate_dir = mc.direction_from_regime_id(cur.get("regime"), mode)
        labels = rg.classify_series(model, candles, "1h", 0.55, 0)
        last = next(lb for lb in reversed(labels) if lb is not None)
        lab_dir = mc.direction_from_regime_id(last, mode)
        assert gate_dir == lab_dir
        assert gate_dir in mc.DIRECTIONS
        ctx = mc.structural_context("regime_gate", direction=gate_dir,
                                    label=cur.get("label"),
                                    model_fp=mc.model_fingerprint(model))
        assert ctx["state"] == "ok" and ctx["model_fingerprint"]
