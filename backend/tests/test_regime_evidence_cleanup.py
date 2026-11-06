import pytest

from services import dynamic_cleanup, regime_evidence, strategy_release

pytestmark = pytest.mark.unit


def _pr(label, trades, avg):
    return {"regime": label, "label": label, "metrics": {"trades": trades, "avg_pnl": avg}}


def test_evidence_ok_when_all_regimes_thick_and_positive():
    ev = regime_evidence.evaluate([_pr("A", 40, 0.3), _pr("B", 35, 0.1)])
    assert ev["ok"] and ev["spread_avg_pnl"] == 0.2 and not ev["reasons"]


def test_evidence_fails_on_thin_or_negative_regime():
    ev = regime_evidence.evaluate([_pr("A", 40, 0.3), _pr("B", 12, 0.1), _pr("C", 50, -0.2)])
    assert not ev["ok"] and len(ev["reasons"]) == 2
    assert not regime_evidence.evaluate([])["ok"]
    assert regime_evidence.evaluate([_pr("A", 0, 0)])["regimes"] == []


def test_structure_warnings():
    assert len(regime_evidence.structure_warnings({"model": {"regime_mode": 9, "timeframe": "1h"}})) == 2
    assert regime_evidence.structure_warnings({"model": {"regime_mode": 5, "timeframe": "4h"}}) == []


def test_initial_release_respects_regime_evidence_flag():
    doc = {"strategy_id": "x"}
    assert strategy_release.initial_release(doc, {"dynamic_better": True})["status"] == "validated"
    assert strategy_release.initial_release(
        doc, {"dynamic_better": True, "regime_evidence_ok": True})["status"] == "validated"
    assert strategy_release.initial_release(
        doc, {"dynamic_better": True, "regime_evidence_ok": False})["status"] == "draft"


def test_evidence_bundle_blocks_on_weak_regimes():
    doc = {"id": "d1", "release": {"status": "draft"}, "model": {"regime_mode": 9, "timeframe": "1h"}}
    out = strategy_release.evidence_bundle(doc, None, {"level": "ok"})
    assert any("9-Regime" in w for w in out["warnings"])
    assert out["validation"]["regime_evidence"] is None


@pytest.mark.parametrize("name,hit", [
    ("TEST_iter15_subs", True), ("TEST_dyn_bollinger", True), ("AP04-Test-1", True),
    ("my_test", True), ("Backtest Champion", False), ("TrendFolge2 dyn", False), ("Testnet", False),
])
def test_is_test_name(name, hit):
    assert dynamic_cleanup.is_test_name(name) is hit


def test_classify_skips_enabled_and_approved():
    docs = [{"id": "a", "name": "TEST_a"}, {"id": "b", "name": "TEST_b"},
            {"id": "c", "name": "AP04-Test-c", "release": {"status": "approved"}},
            {"id": "d", "name": "Echt"}, {"id": "e", "name": "TEST_e", "archived": True}]
    res = dynamic_cleanup.classify(docs, ["b"])
    assert [r["id"] for r in res["archive"]] == ["a"]
    assert {r["id"] for r in res["skipped"]} == {"b", "c"}
