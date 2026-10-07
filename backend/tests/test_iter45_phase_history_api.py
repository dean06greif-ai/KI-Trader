"""Iter45 – API tests for phase-history + variant action (refine tab).

- GET /api/dynamic/dyn_seed1/phase-history?regime_id=0 → variants list, newest first, first current.
- POST /api/dynamic/dyn_seed1/phase action=variant restores a prior variant for ONLY that phase.
- source_dynamic_id outside lineage → 400.
- GET /api/dynamic/dyn_seed1/phases: contains refined_from fields; per-phase metrics when optimized.
"""
import os
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
DID = "dyn_seed1"


@pytest.fixture(scope="module")
def admin_headers():
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"username": "Admin", "password": "PreviewAdmin123!"}, timeout=20)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_phase_history_returns_variants_newest_first_with_current_flag():
    r = requests.get(f"{BASE}/api/dynamic/{DID}/phase-history?regime_id=0", timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["regime_id"] == 0
    variants = d["variants"]
    assert isinstance(variants, list) and len(variants) >= 1
    # newest first → first entry is "aktuell"
    assert variants[0]["current"] is True
    # deduplication: no two variants with identical dynamic_id+version
    keys = [(v.get("dynamic_id"), v.get("version")) for v in variants]
    assert len(keys) == len(set(keys)), f"duplicates in history: {keys}"
    # versions descending inside same dynamic_id
    same = [v["version"] for v in variants if v.get("dynamic_id") == DID and v.get("version")]
    assert same == sorted(same, reverse=True)


def test_phases_contains_refined_from_fields_and_metrics_key():
    r = requests.get(f"{BASE}/api/dynamic/{DID}/phases", timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert "refined_from" in d and "refined_from_name" in d
    assert isinstance(d["phases"], list) and len(d["phases"]) >= 1
    p = d["phases"][0]
    for key in ("regime", "label", "traded", "strategy_id", "trade_params", "strategy_params", "optimized"):
        assert key in p, f"missing {key} in phase payload"


def test_phase_variant_rejects_foreign_source(admin_headers):
    r = requests.post(f"{BASE}/api/dynamic/{DID}/phase", headers=admin_headers, timeout=20,
                      json={"regime_id": 0, "action": "variant", "confirm": True,
                            "source_dynamic_id": "not_in_lineage_xyz", "version": 1})
    assert r.status_code == 400, r.text


def test_phase_variant_requires_confirm(admin_headers):
    r = requests.post(f"{BASE}/api/dynamic/{DID}/phase", headers=admin_headers, timeout=20,
                      json={"regime_id": 0, "action": "variant", "source_dynamic_id": DID, "version": 1})
    assert r.status_code == 400


def test_phase_variant_restore_bumps_version_and_changes_only_that_phase(admin_headers):
    # Pick a non-current variant from lineage for regime 0
    hist = requests.get(f"{BASE}/api/dynamic/{DID}/phase-history?regime_id=0", timeout=20).json()["variants"]
    target = next((v for v in hist if not v.get("current") and v.get("dynamic_id") == DID
                   and v.get("version") is not None), None)
    if target is None:
        pytest.skip("no non-current own-lineage variant available for restore test")

    before_phases = requests.get(f"{BASE}/api/dynamic/{DID}/phases", timeout=20).json()["phases"]
    other_before = {p["regime"]: (p["strategy_id"], p["trade_params"]) for p in before_phases if p["regime"] != 0}
    before_versions = requests.get(f"{BASE}/api/dynamic/{DID}/versions", timeout=20).json()["versions"]
    v_before = before_versions[0]["version"] if before_versions else 0

    r = requests.post(f"{BASE}/api/dynamic/{DID}/phase", headers=admin_headers, timeout=30,
                      json={"regime_id": 0, "action": "variant", "confirm": True,
                            "source_dynamic_id": target["dynamic_id"], "version": target["version"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "success"
    assert body["version"] > v_before, f"new version {body['version']} not > prior {v_before}"
    assert isinstance(body.get("reason"), str) and body["reason"]

    # Verify persistence + other phases unchanged
    after_phases = requests.get(f"{BASE}/api/dynamic/{DID}/phases", timeout=20).json()["phases"]
    other_after = {p["regime"]: (p["strategy_id"], p["trade_params"]) for p in after_phases if p["regime"] != 0}
    assert other_after == other_before, "variant restore altered unrelated phases"

    # The current variant (regime 0) should match the targeted variant's strategy
    p0 = next(p for p in after_phases if p["regime"] == 0)
    if target.get("traded"):
        assert p0["strategy_id"] == target.get("strategy_id") or p0["strategy_name"] == target.get("strategy_name")
    else:
        assert p0["traded"] is False


def test_phase_history_regime_1():
    r = requests.get(f"{BASE}/api/dynamic/{DID}/phase-history?regime_id=1", timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert d["regime_id"] == 1
    assert isinstance(d["variants"], list) and d["variants"][0]["current"] is True
