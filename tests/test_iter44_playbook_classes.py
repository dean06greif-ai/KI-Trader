"""Iter44 – verify /api/ai/playbook returns class-scoped maturity payload."""
import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")


def _backend_up() -> bool:
    try:
        return requests.get(f"{BASE_URL}/api/health", timeout=5).status_code == 200
    except Exception:  # noqa: BLE001
        return False


# E2E: braucht ein laufendes Backend (lokal :8001 oder REACT_APP_BACKEND_URL)
pytestmark = pytest.mark.skipif(not _backend_up(),
                                reason=f"Backend nicht erreichbar: {BASE_URL}")

EXPECTED_CLASSES = ["crypto", "indices", "resources", "forex"]


def _get_playbook():
    r = requests.get(f"{BASE_URL}/api/ai/playbook", timeout=90)
    assert r.status_code == 200, f"status={r.status_code} body={r.text[:400]}"
    return r.json()


def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200
    assert r.json().get("status") == "alive"


def test_login_returns_token():
    import os
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": os.environ.get("ADMIN_USER", "Admin"),
              "password": os.environ.get("ADMIN_PASSWORD", "")},
        timeout=15,
    )
    if r.status_code == 401 and not os.environ.get("ADMIN_PASSWORD"):
        import pytest
        pytest.skip("ADMIN_PASSWORD nicht gesetzt – Login-E2E übersprungen")
    assert r.status_code == 200, r.text[:200]
    assert "token" in r.json() or "access_token" in r.json()


def test_ai_status_reachable():
    r = requests.get(f"{BASE_URL}/api/ai/status", timeout=30)
    assert r.status_code == 200, r.text[:200]


def test_playbook_root_shape():
    pb = _get_playbook()
    assert "setups" in pb and isinstance(pb["setups"], dict)
    assert len(pb["setups"]) >= 16, f"got {len(pb['setups'])} setups"
    assert isinstance(pb.get("maturity"), list)
    assert len(pb["maturity"]) >= 16
    assert pb.get("hard_locks") is False
    rules = pb.get("rules") or {}
    assert "breadth_share" in rules
    assert rules.get("asset_reduce_factor") == 0.5
    assert rules.get("asset_suspend_trades") == 6


def test_playbook_classes_and_order():
    pb = _get_playbook()
    assert pb.get("class_order") == EXPECTED_CLASSES
    classes = pb.get("classes") or {}
    assert set(classes.keys()) == set(EXPECTED_CLASSES)
    for cname, cdata in classes.items():
        assert cdata.get("label")
        assert isinstance(cdata.get("symbols"), list) and len(cdata["symbols"]) > 0
        excluded = cdata.get("excluded") or []
        if cname == "crypto":
            assert excluded == []
        else:
            assert "funding_fade" in excluded
        for k in ("stats", "live_stats", "live_blocked", "live_ready", "revisions", "lifecycle", "maturity"):
            assert k in cdata, f"class {cname} missing {k}"
        rows = cdata["maturity"]
        if cname == "crypto":
            assert len(rows) >= 16
        else:
            # alle Setups außer funding_fade (krypto-only); Anzahl wächst mit neuen Setups
            assert len(rows) >= 15
            assert all(r.get("setup") != "funding_fade" for r in rows)


def test_playbook_maturity_row_fields():
    pb = _get_playbook()
    required = {"setup", "asset_class", "trades", "winrate", "pnl", "live_trades",
                "verdict", "live_ready", "phase", "assets", "revision", "reason", "profile"}
    for cname, cdata in pb["classes"].items():
        for row in cdata["maturity"]:
            missing = required - set(row.keys())
            assert not missing, f"class {cname} setup {row.get('setup')} missing {missing}"
            assert isinstance(row["live_ready"], bool)
            assert row["phase"] in ("live", "sammelt", "rückgestuft", "ruckgestuft")
            assert isinstance(row["assets"], list)
            for a in row["assets"]:
                for f in ("symbol", "trades", "winrate", "pnl", "factor", "state"):
                    assert f in a, f"asset row missing {f} in {row['setup']}/{cname}"


def test_global_live_ready_is_or_of_classes():
    pb = _get_playbook()
    global_map = {r["setup"]: bool(r.get("live_ready")) for r in pb["maturity"]}
    per_setup_or = {}
    for cname, cdata in pb["classes"].items():
        for row in cdata["maturity"]:
            per_setup_or[row["setup"]] = per_setup_or.get(row["setup"], False) or bool(row.get("live_ready"))
    for setup, gval in global_map.items():
        assert gval == per_setup_or.get(setup, False), f"mismatch {setup}: global={gval} or={per_setup_or.get(setup)}"
