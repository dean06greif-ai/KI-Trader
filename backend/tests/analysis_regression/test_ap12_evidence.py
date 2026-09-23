"""AP12: gestufte Abnahme – Beweispaket (read-only) – Solltests.

Vertrag: `strategy_release.evidence_bundle` bündelt Datensatz-/Release-/
Validierungs-/Anwendungs-/Runtime-Belege, benennt Blocker in Klartext und
empfiehlt `ready_for_live` NUR bei leerer Blocker-Liste (kein Profitfaktor
als alleinige Freigabe). Die menschliche Live-Freigabe bleibt Nutzer-Aktion.
"""
import ast
from pathlib import Path

from services import strategy_release as sr

BACKEND = Path(__file__).resolve().parents[2]
FRONTEND = BACKEND.parent / "frontend"

WF_OK = {"verdict": {"dynamic_better": True},
         "label_basis": "causal_live", "attempt_no": 2,
         "created_at": "2026-06-10T00:00:00+00:00",
         "dynamic_test": {"pnl": 12.0, "trades": 20},
         "best_single": {"label": "ema_pullback"}}


def _doc(**kw):
    d = {"id": "dyn1", "name": "Test", "timeframe": "15m",
         "symbols": ["BTCUSDT"], "created_at": "2026-06-09T00:00:00+00:00",
         "settings": {"analysis_id": "ra1"},
         "strategy_id": "s1", "configs": {}}
    d["release"] = {"status": "validated", "revision": 1,
                    "fingerprint": sr.definition_fingerprint(d)}
    d.update(kw)
    return d


def _analysis(**kw):
    a = {"id": "ra1",
         "dataset": {"per_symbol": {"BTCUSDT": {"bars": 100, "hash": "abc"}}},
         "walkforward": {"coin:BTCUSDT": dict(WF_OK)},
         "assignments": {}}
    a.update(kw)
    return a


class TestEvidenceBundle:
    def test_ready_when_all_green(self):
        b = sr.evidence_bundle(_doc(), _analysis(), {"level": "ok"})
        assert b["ready_for_live"] is True and b["blockers"] == []
        assert b["validation"]["passed"] is True
        assert b["validation"]["label_basis"] == "causal_live"
        assert b["dataset"]["status"] == "pinned"

    def test_draft_release_blocks(self):
        d = _doc()
        d["release"]["status"] = "draft"
        b = sr.evidence_bundle(d, _analysis())
        assert b["ready_for_live"] is False
        assert any("Entwurf" in x for x in b["blockers"])

    def test_stale_definition_blocks(self):
        d = _doc()
        d["strategy_id"] = "geändert"  # Fingerprint passt nicht mehr
        b = sr.evidence_bundle(d, _analysis())
        assert any("geändert" in x for x in b["blockers"])

    def test_legacy_doc_not_ready(self):
        d = _doc()
        d.pop("release")
        b = sr.evidence_bundle(d, _analysis())
        assert b["ready_for_live"] is False
        assert any("legacy" in x for x in b["blockers"])

    def test_missing_analysis_blocks(self):
        b = sr.evidence_bundle(_doc(), None)
        assert b["dataset"]["status"] == "missing_analysis"
        assert any("Datenherkunft" in x for x in b["blockers"])

    def test_unpinned_dataset_blocks(self):
        b = sr.evidence_bundle(_doc(), _analysis(dataset=None))
        assert b["dataset"]["status"] == "legacy_unpinned"
        assert any("gepinnt" in x for x in b["blockers"])

    def test_wf_missing_failed_and_stale_block(self):
        b = sr.evidence_bundle(_doc(), _analysis(walkforward={}))
        assert any("Kein finaler Walk-Forward" in x for x in b["blockers"])
        failed = _analysis()
        failed["walkforward"]["coin:BTCUSDT"]["verdict"] = {"dynamic_better": False}
        b2 = sr.evidence_bundle(_doc(), failed)
        assert any("nicht bestanden" in x for x in b2["blockers"])
        stale = _analysis(assignments={"coin:BTCUSDT:0": {
            "assigned_at": "2026-06-11T00:00:00+00:00"}})
        b3 = sr.evidence_bundle(_doc(), stale)
        assert any("veraltet" in x for x in b3["blockers"])

    def test_failed_application_and_critical_safety_block(self):
        d = _doc(application_status={"status": "failed", "error": "x", "attempts": 2})
        b = sr.evidence_bundle(d, _analysis(), {"level": "critical"})
        assert any("Übernahme fehlgeschlagen" in x for x in b["blockers"])
        assert any("CRITICAL" in x for x in b["blockers"])

    def test_wf_scope_prefers_coin_over_combined(self):
        a = _analysis()
        a["walkforward"]["combined"] = {"verdict": {"dynamic_better": False}}
        b = sr.evidence_bundle(_doc(), a)
        assert b["validation"]["walkforward_key"] == "coin:BTCUSDT"
        assert b["ready_for_live"] is True

    def test_good_pnl_alone_is_not_approval(self):
        # AP12: hoher Gewinn ersetzt keine fehlende Validierung
        d = _doc()
        d["release"]["status"] = "draft"
        a = _analysis()
        a["walkforward"]["coin:BTCUSDT"]["dynamic_test"] = {"pnl": 9999, "trades": 500}
        b = sr.evidence_bundle(d, a)
        assert b["ready_for_live"] is False


class TestWiring:
    def test_router_endpoint_exists_readonly(self):
        src = (BACKEND / "routers" / "dynamic.py").read_text()
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "dynamic_evidence")
        body = ast.unparse(fn)
        assert "evidence_bundle" in body
        assert "update_one" not in body and "insert_one" not in body

    def test_frontend_evidence_ui(self):
        src = (FRONTEND / "src" / "components" / "DynamicPanel.js").read_text()
        assert "dyn-evidence-" in src and "dyn-evidence-ready-" in src
        assert "Beweispaket" in src and "blockers" in src
