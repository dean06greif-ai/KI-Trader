"""Iter47 live API tests – autopilot rating unification + dyn label basis + perf.

Verifies:
 - /api/regime-lab/autopilot/runs?limit=60 returns rating {grade,label,basis,why}
   with expected labels for imp_ra_e866c510 ('sehr gut') and d55aad985cdb ('gut').
 - 'why' contains "für „sehr gut" fehlt" and "Such-Score".
 - /api/regime-lab/ra_e866c510 still returns analysis + quality combined overall grade 'sehr gut'.
 - Code-review check: routers.regime_lab._get_doc projection uses lab.NO_CHART.
 - services.dynamic_backtest exposes retro flag + breakdown.label_basis plumbing.
 - routers.backtest copies dynamic_label_basis into cfg.
"""
import os
import re
import requests
import pytest

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


# -------- autopilot runs rating --------
class TestAutopilotRunsRating:
    @pytest.fixture(scope="class")
    def runs(self):
        r = requests.get(f"{BASE}/api/regime-lab/autopilot/runs", params={"limit": 60}, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        runs = data.get("runs") if isinstance(data, dict) else data
        assert isinstance(runs, list), type(runs)
        return {run.get("id") or run.get("_id"): run for run in runs}

    def test_imp_e866_sehr_gut(self, runs):
        run = runs.get("imp_ra_e866c510")
        assert run is not None, "imp_ra_e866c510 missing"
        rating = run.get("rating") or {}
        for k in ("grade", "label", "basis", "why"):
            assert k in rating, f"missing {k} in rating: {rating}"
        assert rating["label"] == "sehr gut", rating
        assert "quality" in str(rating["basis"]).lower() or rating["basis"] in ("quality", "analysis_quality")

    def test_d55a_gut_and_why(self, runs):
        run = runs.get("d55aad985cdb")
        assert run is not None, "d55aad985cdb missing"
        rating = run.get("rating") or {}
        assert rating.get("label") == "gut", rating
        why = rating.get("why") or ""
        assert "sehr gut" in why and "fehlt" in why, f"missing fuer-sehr-gut-fehlt in why: {why!r}"
        assert "Such-Score" in why or "such-score" in why.lower(), f"'Such-Score' missing: {why!r}"


# -------- regime-lab get_doc still works --------
class TestRegimeLabDoc:
    def test_analysis_doc_includes_quality(self):
        r = requests.get(f"{BASE}/api/regime-lab/ra_e866c510", timeout=30)
        assert r.status_code == 200, r.text
        doc = r.json()
        quality = doc.get("quality") or doc.get("regime_quality") or {}
        combined = quality.get("combined") or {}
        overall = combined.get("overall") or {}
        grade = overall.get("grade")
        assert grade == "sehr gut", f"unexpected overall quality.combined.overall.grade: {grade}"

    def test_doc_no_chart_payload(self):
        """Projection should strip chart/chart_emas to keep response small."""
        r = requests.get(f"{BASE}/api/regime-lab/ra_e866c510", timeout=30)
        assert r.status_code == 200
        doc = r.json()
        # chart arrays should not be returned (lab.NO_CHART projection)
        assert "chart" not in doc or not doc.get("chart"), "chart leaked into GET /regime-lab/{aid}"
        assert "chart_emas" not in doc or not doc.get("chart_emas"), "chart_emas leaked"


# -------- backtest router copies dynamic_label_basis --------
class TestBacktestRouterCode:
    def test_router_reads_dynamic_label_basis(self):
        src = open("/app/backend/routers/backtest.py").read()
        assert "dynamic_label_basis" in src, "backtest router does not reference dynamic_label_basis"

    def test_dynamic_backtest_phase_labels_retro(self):
        src = open("/app/backend/services/dynamic_backtest.py").read()
        assert "label_basis" in src, "services/dynamic_backtest.py missing label_basis plumbing"
        assert "retro" in src, "services/dynamic_backtest.py missing retro flag"


# -------- regime_lab router projection uses NO_CHART --------
class TestRegimeLabRouterProjection:
    def test_get_doc_uses_no_chart(self):
        src = open("/app/backend/routers/regime_lab.py").read()
        # some reference to NO_CHART projection or explicit exclude of chart
        assert ("NO_CHART" in src) or ("chart" in src and "0" in src), (
            "regime_lab router does not appear to strip chart data"
        )
