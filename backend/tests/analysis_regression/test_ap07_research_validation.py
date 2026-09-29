"""AP07-Solltests (Befunde R05/R10): Forschungsvalidierung ohne Holdout-Tuning.

R10: Auswahl (EMA-Periode/Kombi-Score) läuft auf der INNEREN Validierung,
     nie auf dem Holdout; jeder Versuch wird gezählt; zu wenig Holdout-Daten
     heißt `insufficient_evidence`.
R05: Kausale Live-Labels sind präfix-stabil (kein Lookahead); Suchergebnisse
     aus Final-Labels tragen `label_basis=retrospective_reference`, der
     Walkforward `causal_live`.
"""
import asyncio
import inspect

import numpy as np
import pytest

from services import regime as rg
from services import regime_lab as lab
from services import regime_opt
from services import research_validation as rv

pytestmark = pytest.mark.unit


def _candles(n=600, start=1_700_000_000_000, step=60_000, seed=7):
    rng = np.random.default_rng(seed)
    px = 100 + np.cumsum(rng.normal(0, 0.3, n))
    out = []
    for i in range(n):
        o = float(px[i])
        c = float(px[i] + rng.normal(0, 0.1))
        out.append({"timestamp": start + i * step, "open": o,
                    "high": max(o, c) + 0.05, "low": min(o, c) - 0.05,
                    "close": c, "volume": 5.0 + float(rng.uniform(0, 1))})
    return out


class TestR10Selection:
    def test_select_best_never_uses_holdout(self):
        rows = [{"period": 5, "inner_direction_pct": 70.0,
                 "holdout_direction_pct": 40.0},
                {"period": 9, "inner_direction_pct": 55.0,
                 "holdout_direction_pct": 95.0}]  # Holdout-Sieger, inner Verlierer
        best, basis = rv.select_best_row(rows)
        assert best["period"] == 5 and basis == "inner_validation"

    def test_fallback_is_train_not_holdout(self):
        rows = [{"period": 5, "direction_pct": 60.0,
                 "holdout_direction_pct": 99.0},
                {"period": 9, "direction_pct": 65.0,
                 "holdout_direction_pct": 10.0}]
        best, basis = rv.select_best_row(rows)
        assert best["period"] == 9 and basis == "train_only"
        assert rv.select_best_row([])[1] == "none"

    def test_inner_anchor_within_train_window(self):
        candles = _candles(400)
        cut = 300
        ts = rv.inner_anchor_ts(candles, cut)
        assert candles[0]["timestamp"] < ts < candles[cut - 1]["timestamp"]
        assert rv.inner_anchor_ts(candles, 4) is None

    def test_evidence_verdict(self):
        assert rv.evidence_verdict(10) == "insufficient_evidence"
        assert rv.evidence_verdict(None) == "insufficient_evidence"
        assert rv.evidence_verdict(200) == "ok"

    def test_ema_and_kombi_wiring(self):
        """Struktureller Nachweis: Auswahl/Score nutzen die innere Validierung,
        nicht mehr max(holdout_direction_pct)."""
        src = inspect.getsource(lab.run_ema_compare)
        assert "select_best_row" in src and "register_attempt" in src
        assert 'key=lambda r: r["holdout_direction_pct"]' not in src
        srck = inspect.getsource(lab.run_kombi_calibrate)
        assert "inner_direction_pct" in srck
        assert "score = (sel or 0.0)" in srck  # sel = inner, nicht hold

    def test_attempt_counter_increments(self):
        class FakeColl:
            def __init__(self):
                self.docs = {}

            async def update_one(self, flt, update, upsert=False):
                d = self.docs.setdefault(flt["scope"], {"scope": flt["scope"],
                                                        "attempts": 0})
                d["attempts"] += update["$inc"]["attempts"]
                d.update(update.get("$set") or {})

            async def find_one(self, flt):
                return self.docs.get(flt["scope"])

        class FakeDB:
            research_attempts = FakeColl()

        db = FakeDB()

        async def run():
            a1 = await rv.register_attempt(db, "walkforward:x:combined", "walkforward")
            a2 = await rv.register_attempt(db, "walkforward:x:combined", "walkforward")
            return a1, a2

        a1, a2 = asyncio.new_event_loop().run_until_complete(run())
        assert (a1, a2) == (1, 2)
        assert rv.experiment_manifest("ema_compare", {"periods": [5]})[
            "holdout_role"] == "final_test"


class TestR10InnerWindowMetrics:
    def test_inner_and_holdout_windows_are_disjoint(self):
        candles = _candles(200)
        model = rg.detect_regimes({"BTC": candles[:150]}, "1m", 3, 0.02,
                                  engine="kmeans")
        assert model
        labels = [0] * 200
        train_end = candles[149]["timestamp"]
        inner_start = candles[99]["timestamp"]
        la = lab._live_agreement(candles, labels, labels, model,
                                 train_end_ts=train_end,
                                 inner_start_ts=inner_start)
        assert la["holdout_bars"] == 50           # Kerzen 150..199
        assert la["inner_bars"] == 50             # Kerzen 100..149 (nur Training)
        assert la["inner_direction_pct"] == 100.0
        assert la["bars"] == 200


class TestR05CausalLabels:
    def test_live_labels_are_prefix_stable(self):
        """Positiv-Fixture: kausale Live-Klassifikation ändert sich nicht,
        wenn ZUKÜNFTIGE Kerzen angehängt werden (kein Lookahead)."""
        candles = _candles(600, seed=11)
        model = rg.detect_regimes({"BTC": candles[:400]}, "1m", 3, 0.02,
                                  engine="kmeans")
        assert model
        full = rg.classify_series(model, candles, "1m", 0.55, 0)
        prefix = rg.classify_series(model, candles[:480], "1m", 0.55, 0)
        assert full[:480] == prefix, "Live-Labels sind nicht präfix-stabil"

    def test_result_payloads_carry_label_basis(self):
        """Negativ-/Kennzeichnungs-Fixture: Suche = retrospective_reference,
        Walkforward = causal_live (kausal via classify_series)."""
        src_opt = inspect.getsource(regime_opt.run_regime_optimizer)
        assert '"label_basis": "retrospective_reference"' in src_opt
        src_wf = inspect.getsource(regime_opt.run_walkforward)
        assert '"label_basis": "causal_live"' in src_wf
        assert "classify_series" in src_wf
        assert "register_attempt" in src_wf
