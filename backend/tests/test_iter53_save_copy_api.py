"""Iter53 live API tests: Optimizer save_copy, duplicate regression, dynamic
workbench regime_modes validation. Uses REACT_APP_BACKEND_URL."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"},
                      timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def created_ids():
    ids = []
    yield ids
    # Cleanup
    tok = requests.post(f"{BASE_URL}/api/auth/login",
                       json={"username": "Admin", "password": "Dean06Greif!/Admin"},
                       timeout=30).json()["token"]
    h = {"Authorization": f"Bearer {tok}"}
    for sid in ids:
        try:
            requests.delete(f"{BASE_URL}/api/strategies/{sid}", headers=h, timeout=30)
        except Exception:
            pass


def _get_strategies(auth):
    r = requests.get(f"{BASE_URL}/api/strategies", headers=auth, timeout=30)
    assert r.status_code == 200
    data = r.json()
    # strategies list may be under "strategies"
    return data.get("strategies") or data.get("items") or data


def _settings(auth):
    r = requests.get(f"{BASE_URL}/api/settings", headers=auth, timeout=30)
    assert r.status_code == 200
    return r.json()


# ------ save_copy for built-in strategy (rsi_only) ------
class TestSaveCopyVariant:
    def test_save_copy_rsi_only_creates_variant(self, auth, created_ids):
        # Base settings before
        s_before = _settings(auth)
        rsi_params_before = dict((s_before.get("strategy_params") or {}).get("rsi_only", {}))
        enabled_before = set(s_before.get("enabled_strategies") or [])

        body = {
            "type": "save_copy",
            "strategy_id": "rsi_only",
            "params": {"rsi_period": 9},
            "trade_params": {"tp_percent": 1.2},
            "timeframe": "15m",
            "metrics": {"pnl": 1, "trades": 2, "win_rate": 50},
            "rank": 2,
        }
        r = requests.post(f"{BASE_URL}/api/optimizer/apply", json=body,
                          headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "success"
        sid = data["id"]
        assert sid.startswith("variant_"), f"expected variant_, got {sid}"
        assert data["kind"] == "variant"
        assert "Optimizer #2" in data["name"], f"name={data['name']}"
        created_ids.append(sid)

        # New strategy appears in GET /api/strategies
        strategies = _get_strategies(auth)
        ids = {s.get("id") for s in strategies}
        assert sid in ids

        # Not in enabled_strategies
        s_after = _settings(auth)
        enabled_after = set(s_after.get("enabled_strategies") or [])
        assert sid not in enabled_after
        # Base enabled unchanged (no new enables)
        assert enabled_after >= enabled_before or enabled_after == enabled_before

        # strategy_params[new_id] contains the saved params
        sp = (s_after.get("strategy_params") or {}).get(sid, {})
        assert sp.get("rsi_period") == 9

        # Base rsi_only params not changed by save_copy
        rsi_params_after = dict((s_after.get("strategy_params") or {}).get("rsi_only", {}))
        assert rsi_params_after == rsi_params_before, \
            f"rsi_only params changed: before={rsi_params_before} after={rsi_params_after}"

    def test_save_copy_with_definition_creates_custom(self, auth, created_ids):
        body = {
            "type": "save_copy",
            "definition": {
                "name": "Disc X",
                "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}],
                "short_rules": [],
            },
            "timeframe": "5m",
            "metrics": {"pnl": 0.5, "trades": 3, "win_rate": 33},
        }
        r = requests.post(f"{BASE_URL}/api/optimizer/apply", json=body,
                          headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["kind"] == "custom"
        assert data["id"].startswith("custom_")
        created_ids.append(data["id"])

        # Verify timeframe==5m in settings/strategy_timeframes
        s = _settings(auth)
        tfs = s.get("strategy_timeframes") or {}
        assert tfs.get(data["id"]) == "5m"

    def test_save_copy_missing_source_returns_400(self, auth):
        r = requests.post(f"{BASE_URL}/api/optimizer/apply",
                          json={"type": "save_copy"}, headers=auth, timeout=30)
        assert r.status_code == 400

    def test_save_copy_ai_trader_returns_400(self, auth):
        r = requests.post(f"{BASE_URL}/api/optimizer/apply",
                          json={"type": "save_copy", "strategy_id": "ai_trader"},
                          headers=auth, timeout=30)
        assert r.status_code == 400


# ------ Duplicate regression ------
class TestDuplicateRegression:
    def test_duplicate_rsi_only_is_variant(self, auth, created_ids):
        r = requests.post(f"{BASE_URL}/api/strategies/rsi_only/duplicate",
                          json={}, headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("is_variant") is True
        assert data.get("is_custom") is False
        assert data["id"].startswith("variant_")
        created_ids.append(data["id"])

    def test_duplicate_custom_is_custom(self, auth, created_ids):
        # First create a custom via save_copy
        r = requests.post(f"{BASE_URL}/api/optimizer/apply",
                          json={"type": "save_copy",
                                "definition": {"name": "Dup Src",
                                               "long_rules": [{"indicator": "rsi", "op": "<", "value": 25}],
                                               "short_rules": []},
                                "timeframe": "15m"}, headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        src = r.json()["id"]
        created_ids.append(src)

        r2 = requests.post(f"{BASE_URL}/api/strategies/{src}/duplicate",
                           json={}, headers=auth, timeout=30)
        assert r2.status_code == 200, r2.text
        d = r2.json()
        assert d.get("is_custom") is True
        assert d.get("is_variant") is False
        assert d["id"].startswith("custom_")
        created_ids.append(d["id"])


# ------ Apply type=params/strategy still working ------
class TestApplyRegression:
    def test_apply_params_still_works(self, auth):
        s_before = _settings(auth)
        rsi_before = dict((s_before.get("strategy_params") or {}).get("rsi_only", {}))
        r = requests.post(f"{BASE_URL}/api/optimizer/apply",
                          json={"type": "params", "strategy_id": "rsi_only",
                                "params": {"rsi_period": rsi_before.get("rsi_period", 14)},
                                "trade_params": {}, "timeframe": None},
                          headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["status"] == "success"
        assert d["strategy_id"] == "rsi_only"


# ------ Dynamic workbench regime_modes validation ------
class TestWorkbenchRegimeModesValidation:
    def test_invalid_regime_mode_returns_400_before_analysis_lookup(self, auth):
        body = {"kind": "discover", "analysis_id": "nonexistent_xyz",
                "regime_ids": [0], "regime_modes": {"0": "foo"}}
        r = requests.post(f"{BASE_URL}/api/dynamic-workbench/start",
                          json=body, headers=auth, timeout=30)
        assert r.status_code == 400, r.text
        detail = (r.json() or {}).get("detail", "")
        assert "regime_modes" in detail.lower(), f"unexpected detail: {detail}"
