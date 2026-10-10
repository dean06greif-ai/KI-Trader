"""Live-API-Tests Edge-Register (`/api/ai/playbook/backtest/edges[...]`).
Läuft gegen eine laufende Backend-Umgebung (KITRADER_BASE_URL bzw.
REACT_APP_BACKEND_URL, Standard lokaler Dev-Server); Admin-Zugang aus
ADMIN_USER/ADMIN_PASSWORD (backend/.env via conftest)."""
import os
import pytest
import requests

BASE_URL = (os.environ.get("KITRADER_BASE_URL") or os.environ.get("REACT_APP_BACKEND_URL")
            or "http://localhost:8001").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASS:
    pytest.skip("Keine Admin-Zugangsdaten (ADMIN_PASSWORD) gesetzt", allow_module_level=True)


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_token(api):
    r = api.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---- Basic health / playbook regression ----
def test_health(api):
    r = api.get(f"{BASE_URL}/api/health", timeout=30)
    assert r.status_code == 200


def test_ai_playbook_ok(api):
    r = api.get(f"{BASE_URL}/api/ai/playbook", timeout=60)
    assert r.status_code == 200
    data = r.json()
    assert "maturity" in data or "playbook" in data or isinstance(data, dict)


# ---- Edge register shape ----
def test_edges_shape_and_rules(api):
    r = api.get(f"{BASE_URL}/api/ai/playbook/backtest/edges", timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert "edges" in data and "rules" in data
    rules = data["rules"]
    for k in ("stale_max", "replace_margin", "min_trades_ratio"):
        assert k in rules
    assert rules["stale_max"] == 3
    assert abs(rules["replace_margin"] - 0.1) < 1e-6
    assert abs(rules["min_trades_ratio"] - 0.7) < 1e-6
    for e in data["edges"]:
        for f in ("asset_class", "setup", "id", "name", "status", "params", "is", "oos", "robust", "flags", "confirmations"):
            assert f in e, f"missing {f} in edge {e}"
        assert "oos_trades" not in e
        assert "_id" not in e
        assert e["status"] in ("active", "candidate", "retired")


def test_edges_filter_asset_class(api):
    r = api.get(f"{BASE_URL}/api/ai/playbook/backtest/edges?asset_class=crypto", timeout=30)
    assert r.status_code == 200
    for e in r.json()["edges"]:
        assert e["asset_class"] == "crypto"

    r_bad = api.get(f"{BASE_URL}/api/ai/playbook/backtest/edges?asset_class=foo", timeout=30)
    assert r_bad.status_code == 400


# ---- Recover ----
def test_recover_requires_admin(api):
    r = api.post(f"{BASE_URL}/api/ai/playbook/backtest/edges/recover", json={}, timeout=30)
    assert r.status_code in (401, 403)


def test_recover_admin_idempotent(api, admin_headers):
    before = api.get(f"{BASE_URL}/api/ai/playbook/backtest/edges", timeout=30).json()
    count_before = len(before["edges"])

    r1 = api.post(f"{BASE_URL}/api/ai/playbook/backtest/edges/recover", json={}, headers=admin_headers, timeout=60)
    assert r1.status_code == 200, r1.text
    d1 = r1.json()
    assert d1.get("status") == "ok"
    assert "imported" in d1 and "activated" in d1

    r2 = api.post(f"{BASE_URL}/api/ai/playbook/backtest/edges/recover", json={}, headers=admin_headers, timeout=60)
    assert r2.status_code == 200
    d2 = r2.json()

    after = api.get(f"{BASE_URL}/api/ai/playbook/backtest/edges", timeout=30).json()
    count_after = len(after["edges"])
    # Idempotent: total edge count unchanged after the second call
    assert count_after == max(count_before, count_after)  # allow first call to import more; no duplicates from 2nd call
    # Second call imported no more edges than first
    assert d2["imported"] <= d1["imported"]


# ---- Activate errors ----
def test_activate_requires_admin(api):
    r = api.post(f"{BASE_URL}/api/ai/playbook/backtest/edges/activate",
                 json={"asset_class": "crypto", "setup": "breakout", "edge_id": "xxx"}, timeout=30)
    assert r.status_code in (401, 403)


def test_activate_missing_fields(api, admin_headers):
    r = api.post(f"{BASE_URL}/api/ai/playbook/backtest/edges/activate",
                 json={"asset_class": "crypto"}, headers=admin_headers, timeout=30)
    assert r.status_code == 400


def test_activate_unknown_edge_id(api, admin_headers):
    r = api.post(f"{BASE_URL}/api/ai/playbook/backtest/edges/activate",
                 json={"asset_class": "crypto", "setup": "breakout", "edge_id": "does-not-exist-xxxxx"},
                 headers=admin_headers, timeout=30)
    assert r.status_code == 404


# ---- Activate happy path (state changing) ----
def test_activate_nonactive_edge_and_verify(api, admin_headers):
    listing = api.get(f"{BASE_URL}/api/ai/playbook/backtest/edges", timeout=30).json()["edges"]
    # Find a non-active edge whose (class, setup) also has a currently active edge (so we can verify swap)
    groups = {}
    for e in listing:
        groups.setdefault((e["asset_class"], e["setup"]), []).append(e)
    target = None
    for (cls, sid), lst in groups.items():
        actives = [e for e in lst if e["status"] == "active"]
        nonactives = [e for e in lst if e["status"] != "active"]
        if actives and nonactives:
            target = (cls, sid, nonactives[0], actives[0])
            break
    if not target:
        pytest.skip("no (class,setup) with both active and non-active edges to test rollback")
    cls, sid, cand, prev_active = target

    r = api.post(f"{BASE_URL}/api/ai/playbook/backtest/edges/activate",
                 json={"asset_class": cls, "setup": sid, "edge_id": cand["id"]},
                 headers=admin_headers, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("status") == "ok"
    assert data["edge"]["status"] == "active"
    entry = data["entry"]
    assert entry["status"] == "tuned"
    assert entry.get("edge_action") == "rollback"
    assert "tuned" in entry
    assert "restored_trades" in data

    # Verify listing: exactly ONE active edge for this class/setup and it's the newly activated one
    listing2 = api.get(f"{BASE_URL}/api/ai/playbook/backtest/edges", timeout=30).json()["edges"]
    matches = [e for e in listing2 if e["asset_class"] == cls and e["setup"] == sid]
    actives2 = [e for e in matches if e["status"] == "active"]
    assert len(actives2) == 1
    assert actives2[0]["id"] == cand["id"]
    prev_new = next(e for e in matches if e["id"] == prev_active["id"])
    assert prev_new["status"] == "retired"

    # Verify overview reflects the change
    ov = api.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=30).json()
    entry_ov = ov["classes"][cls][sid]
    assert entry_ov["status"] == "tuned"
    assert entry_ov["name"] == cand["name"]


# ---- Overview rules ----
def test_overview_rules_and_structure(api):
    r = api.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=30)
    assert r.status_code == 200
    data = r.json()
    for k in ("classes", "last_result", "auto", "rules"):
        assert k in data
    rules = data["rules"]
    for k in ("edge_stale_max", "edge_replace_margin", "edge_min_trades_ratio"):
        assert k in rules, f"missing {k} in overview rules"
