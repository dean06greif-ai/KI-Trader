"""AP10 (R17): verständliche UI-Zustände – Solltests.

Backend-Vertrag: differenzierter Walk-Forward-Status (nicht geprüft / bestanden /
nicht bestanden / veraltet) + Verdrahtung in der Analyse-Liste.
Frontend-Vertrag: die neuen Zustands-Anzeigen existieren als data-testids
(Quelltext-Regression – Browserlauf macht der Testing-Agent).
"""
import ast
from pathlib import Path

from services import research_validation as rv

BACKEND = Path(__file__).resolve().parents[2]
FRONTEND = BACKEND.parent / "frontend"


def _wf(passed: bool, created_at: str = "2026-06-01T00:00:00+00:00"):
    return {"verdict": {"dynamic_better": passed}, "created_at": created_at}


class TestWalkforwardStatus:
    def test_no_walkforward(self):
        assert rv.walkforward_status({}) == {"passed": None, "stale": False}
        assert rv.walkforward_status({"walkforward": {}}) == {
            "passed": None, "stale": False}

    def test_passed_and_failed(self):
        assert rv.walkforward_status(
            {"walkforward": {"combined": _wf(True)}})["passed"] is True
        assert rv.walkforward_status(
            {"walkforward": {"combined": _wf(False)}})["passed"] is False

    def test_mixed_scopes_not_passed(self):
        doc = {"walkforward": {"combined": _wf(True),
                               "coin:BTCUSDT": _wf(False)}}
        assert rv.walkforward_status(doc)["passed"] is False

    def test_stale_when_assignment_changed_after_wf(self):
        doc = {"walkforward": {"combined": _wf(True, "2026-06-01T00:00:00+00:00")},
               "assignments": {"combined:0": {
                   "assigned_at": "2026-06-02T00:00:00+00:00"}}}
        st = rv.walkforward_status(doc)
        assert st["stale"] is True and st["passed"] is True

    def test_not_stale_when_assignment_older(self):
        doc = {"walkforward": {"combined": _wf(True, "2026-06-05T00:00:00+00:00")},
               "assignments": {"combined:0": {
                   "assigned_at": "2026-06-02T00:00:00+00:00"}}}
        assert rv.walkforward_status(doc)["stale"] is False

    def test_other_scope_assignment_does_not_stale(self):
        doc = {"walkforward": {"combined": _wf(True, "2026-06-01T00:00:00+00:00")},
               "assignments": {"coin:BTCUSDT:1": {
                   "assigned_at": "2026-06-09T00:00:00+00:00"}}}
        assert rv.walkforward_status(doc)["stale"] is False

    def test_missing_created_at_is_safe(self):
        doc = {"walkforward": {"combined": {"verdict": {"dynamic_better": True}}},
               "assignments": {"combined:0": {
                   "assigned_at": "2026-06-02T00:00:00+00:00"}}}
        st = rv.walkforward_status(doc)
        assert st == {"passed": True, "stale": False}


class TestListWiring:
    def test_list_endpoint_uses_helper(self):
        src = (BACKEND / "routers" / "regime_lab.py").read_text()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "list_analyses")
        body = ast.unparse(fn)
        assert "walkforward_status" in body
        assert "walkforward_stale" in body
        assert "walkforward_passed" in body


class TestFrontendStateMarkers:
    """Quelltext-Regression: die AP10-Zustände existieren in der UI."""

    def test_regimelab_wf_states(self):
        src = (FRONTEND / "src" / "components" / "RegimeLab.js").read_text()
        assert "WF veraltet" in src
        assert "WF nicht geprüft" in src
        assert "WF nicht bestanden" in src
        assert "walkforward_stale" in src

    def test_regimelab_wf_provenance(self):
        src = (FRONTEND / "src" / "components" / "RegimeLab.js").read_text()
        assert "regime-wf-provenance-" in src
        assert "label_basis" in src and "attempt_no" in src
        # R17: positive Gestaltung nur bei tatsächlich besserem Verdict
        assert '"opt-card best"' not in src
        assert "wf.verdict?.dynamic_better ? 'best' : ''" in src

    def test_regimelab_dataset_status_in_detail(self):
        src = (FRONTEND / "src" / "components" / "RegimeLab.js").read_text()
        assert "regime-detail-dataset" in src
        assert "Daten nicht gepinnt" in src and "Daten gepinnt" in src

    def test_dynamicpanel_applied_state(self):
        src = (FRONTEND / "src" / "components" / "DynamicPanel.js").read_text()
        assert "dyn-apply-status-" in src
        assert "application_status" in src
        assert "Übernahme fehlgeschlagen" in src
        assert "Übernahme blockiert" in src
        assert "dyn-last-applied-" in src
