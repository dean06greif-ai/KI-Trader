"""Iter14 backend tests: Regime-Lab calibration history, engine snapshots,
shadow backfill, rename & scope-missing metadata."""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://autopilot-stable-2.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

SEED_AID = "ra_ca0b956f"
SEED_AID_DEL = "ra_b62d7dcf"
SEED_AUTOPILOT_ID = "aptest001"
ORIG_NAME = "Test · BTC + Öl (Öl ohne Daten)"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=45)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- calibrations history ---
class TestCalibrationHistory:
    def test_history_includes_autopilot_seed(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/calibrations", params={"limit": 20}, timeout=45)
        assert r.status_code == 200
        rows = r.json().get("calibrations", [])
        assert isinstance(rows, list) and len(rows) > 0
        ap = [x for x in rows if x.get("id") == SEED_AUTOPILOT_ID]
        assert ap, f"Seed autopilot row {SEED_AUTOPILOT_ID} not found"
        row = ap[0]
        assert row.get("source") == "autopilot"
        rep = row.get("report") or {}
        assert rep.get("metric") == "holdout_direction_pct"
        assert rep.get("holdout_regressed") is True
        assert isinstance(rep.get("best_config"), dict) and rep["best_config"]
        # verify science rows present too
        sci = [x for x in rows if x.get("source") == "calibration"]
        assert sci, "expected at least one scientific calibration row"
        assert (sci[0].get("report") or {}).get("metric") == "balanced_direction_pct"

    def test_history_exclude_autopilot(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/calibrations",
                         params={"limit": 20, "include_autopilot": "false"}, timeout=45)
        assert r.status_code == 200
        rows = r.json().get("calibrations", [])
        assert all(x.get("source") != "autopilot" for x in rows)
        assert not any(x.get("id") == SEED_AUTOPILOT_ID for x in rows)


# --- engine snapshots ---
class TestEngineSnapshots:
    def test_snapshot_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/regime-lab/engine/snapshots",
                          json={"engine_config": {"a": 1}}, timeout=45)
        assert r.status_code in (401, 403)

    def test_snapshot_missing_engine_config(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/regime-lab/engine/snapshots",
                          headers=admin_headers, json={"reason": "x"}, timeout=45)
        assert r.status_code == 400

    def test_snapshot_create_and_list(self, admin_headers):
        payload = {
            "engine_config": {"truth_source": "centered", "tf": "1h"},
            "calib_applied": {"metric": "balanced_direction_pct", "value": 0.5},
            "reason": "TEST_iter14_snapshot",
        }
        r = requests.post(f"{BASE_URL}/api/regime-lab/engine/snapshots",
                          headers=admin_headers, json=payload, timeout=45)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "success"
        snap = data.get("snapshot") or {}
        assert snap.get("id")
        sid = snap["id"]

        lst = requests.get(f"{BASE_URL}/api/regime-lab/engine/snapshots", timeout=45)
        assert lst.status_code == 200
        snaps = lst.json().get("snapshots", [])
        assert snaps, "snapshots list empty"
        assert snaps[0].get("id") == sid, "newest first ordering broken"
        assert any(s.get("id") == sid for s in snaps)


# --- shadow backfill ---
class TestShadowBackfill:
    def test_backfill_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/regime-lab/shadow/backfill",
                          json={}, timeout=20)
        assert r.status_code in (401, 403)

    def test_backfill_admin_ok(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/regime-lab/shadow/backfill",
                          headers=admin_headers, json={}, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        for k in ("scanned", "labeled", "skipped_no_release", "symbols"):
            assert k in d, f"missing field {k}"
        assert isinstance(d["scanned"], int)
        assert isinstance(d["labeled"], int)
        assert isinstance(d["skipped_no_release"], int)


# --- rename analysis ---
class TestRenameAnalysis:
    def test_rename_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/regime-lab/{SEED_AID}/rename",
                          json={"name": "hack"}, timeout=45)
        assert r.status_code in (401, 403)

    def test_rename_empty_name(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/regime-lab/{SEED_AID}/rename",
                          headers=admin_headers, json={"name": "   "}, timeout=45)
        assert r.status_code == 400

    def test_rename_unknown_aid(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/regime-lab/does_not_exist_xyz/rename",
                          headers=admin_headers, json={"name": "x"}, timeout=45)
        assert r.status_code == 404

    def test_rename_roundtrip_and_reset(self, admin_headers):
        new_name = "TEST_iter14_renamed"
        r = requests.post(f"{BASE_URL}/api/regime-lab/{SEED_AID}/rename",
                          headers=admin_headers, json={"name": new_name}, timeout=45)
        assert r.status_code == 200, r.text

        lst = requests.get(f"{BASE_URL}/api/regime-lab/list", timeout=45).json()
        rows = lst.get("analyses") or lst.get("list") or []
        found = next((x for x in rows if x.get("id") == SEED_AID), None)
        assert found is not None, "seed analysis missing from list"
        assert found.get("name") == new_name

        # reset back
        r2 = requests.post(f"{BASE_URL}/api/regime-lab/{SEED_AID}/rename",
                           headers=admin_headers, json={"name": ORIG_NAME}, timeout=45)
        assert r2.status_code == 200


# --- analysis detail: missing coin metadata ---
class TestAnalysisDetail:
    def test_detail_has_symbols_requested_and_skipped(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/{SEED_AID}", timeout=45)
        assert r.status_code == 200
        data = r.json()
        analysis = data.get("analysis") or {}
        assert analysis.get("symbols_requested") == ["BTCUSDT", "CLUSDT"]
        skipped = analysis.get("symbols_skipped") or {}
        assert "CLUSDT" in skipped
        assert (skipped["CLUSDT"] or {}).get("reason")


# --- regression: existing endpoints still work ---
class TestRegression:
    def test_list(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/list", timeout=45)
        assert r.status_code == 200

    def test_active(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/active", timeout=45)
        assert r.status_code == 200

    def test_engine_defaults(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/engine/defaults", timeout=45)
        assert r.status_code == 200

    def test_autopilot_runs(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/autopilot/runs", timeout=45)
        assert r.status_code == 200

    def test_releases(self):
        r = requests.get(f"{BASE_URL}/api/regime-lab/releases", timeout=45)
        assert r.status_code == 200

    def test_delete_unknown_404(self, admin_headers):
        r = requests.delete(f"{BASE_URL}/api/regime-lab/unknown_does_not_exist",
                            headers=admin_headers, timeout=45)
        assert r.status_code == 404
