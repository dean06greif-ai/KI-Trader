"""Iteration 22: slippage-stats strategy-name resolution, days clamping,
and core endpoint regressions."""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = (os.environ.get("REACT_APP_BACKEND_URL")
            or frontend_env.get("REACT_APP_BACKEND_URL"))
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

SID = "custom_test9999"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# --- Feature 1: Klarnamen in slippage-stats -------------------------------
class TestSlippageNames:
    def test_all_groups_have_strategy_name(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days=30",
                       timeout=60)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data["days"] == 30
        assert isinstance(data["groups"], list)
        for g in data["groups"]:
            assert "strategy_name" in g, g
            assert isinstance(g["strategy_name"], str) and g["strategy_name"]

    def test_custom_strategy_shows_clear_name(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days=30",
                       timeout=60)
        groups = r.json()["groups"]
        mine = [g for g in groups if g.get("strategy_id") == SID]
        assert mine, f"seeded group {SID} not found: {[g.get('strategy_id') for g in groups]}"
        assert mine[0]["strategy_name"] == "Mein Test Strat"

    def test_external_named_manuell_bitunix(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days=365",
                       timeout=60)
        groups = r.json()["groups"]
        ext = [g for g in groups if g.get("strategy_id") == "external"]
        if not ext:
            pytest.skip("no external slippage trades present")
        assert ext[0]["strategy_name"] == "Manuell (Bitunix)"


# --- Feature 7: days clamping --------------------------------------------
@pytest.mark.parametrize("given,expected", [(0, 30), (9999, 365), (-5, 1),
                                            (7, 7)])
def test_days_clamping(client, given, expected):
    r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days={given}",
                   timeout=60)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["days"] == expected


# --- Regression: core endpoints ------------------------------------------
@pytest.mark.parametrize("path", ["/api/ai/status", "/api/strategies",
                                  "/api/settings", "/api/autotrade/config"])
def test_core_endpoints_ok(client, path):
    r = client.get(f"{BASE_URL}{path}", timeout=90)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
    assert r.json() is not None


def test_no_mongo_id_in_slippage(client):
    r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days=30",
                   timeout=60)
    assert '"_id"' not in r.text
