"""Iteration 11 – MAE / trough tracking API tests (read-only)."""
import os
import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("BACKEND_BASE_URL")
            or frontend_env.get("REACT_APP_BACKEND_URL", "")).rstrip("/")
if not BASE_URL:
    raise RuntimeError("No backend base url")

TIMEOUT = 120


@pytest.fixture(scope="module")
def trades():
    r = requests.get(f"{BASE_URL}/api/autotrade/trades?limit=5", timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert isinstance(data.get("trades"), list)
    return data["trades"]


class TestTroughFields:
    def test_status_and_fields_present(self, trades):
        assert len(trades) > 0, "no trades returned"
        for t in trades:
            c = t.get("computed")
            assert c is not None, f"no computed for {t.get('id')}"
            for f in ("trough_price", "trough_distance_pct", "mae_pct",
                      "peak_price", "mfe_pct"):
                assert f in c, f"missing computed.{f} in {t.get('id')}"

    def test_no_mongo_id(self, trades):
        for t in trades:
            assert "_id" not in t

    def test_trough_plausibility(self, trades):
        checked = 0
        for t in trades:
            c = t["computed"]
            tr = c.get("trough_price")
            if tr is None:
                # Alt-Trade ohne trough -> alle abhängigen Felder null
                assert c.get("trough_distance_pct") is None
                assert c.get("mae_pct") is None
                continue
            entry = float(t["entry"])
            side = t["side"].upper()
            cur = c.get("current_price")
            if side == "LONG":
                assert tr <= entry * 1.0000001, f"LONG trough {tr} > entry {entry}"
                if t["status"] == "open" and cur:
                    assert tr <= cur * 1.0000001
            else:
                assert tr >= entry * 0.9999999, f"SHORT trough {tr} < entry {entry}"
                if t["status"] == "open" and cur:
                    assert tr >= cur * 0.9999999
            checked += 1
        assert checked > 0, "no trade with trough_price to validate"

    def test_mae_sign(self, trades):
        for t in trades:
            c = t["computed"]
            if c.get("mae_pct") is None:
                continue
            assert c["mae_pct"] <= 0.0001, f"mae_pct must be <=0, got {c['mae_pct']}"

    def test_mfe_sign(self, trades):
        for t in trades:
            c = t["computed"]
            if c.get("mfe_pct") is None:
                continue
            assert c["mfe_pct"] >= -0.0001


class TestChartEndpoint:
    def test_chart_has_trough_price(self, trades):
        tested = 0
        for t in trades[:3]:
            r = requests.get(f"{BASE_URL}/api/autotrade/trades/{t['id']}/chart",
                             timeout=TIMEOUT)
            assert r.status_code == 200, f"{t['id']}: {r.status_code} {r.text[:200]}"
            body = r.json()
            trade = body.get("trade")
            assert trade is not None, f"no trade obj: {list(body)}"
            assert "trough_price" in trade, f"chart trade missing trough_price: {list(trade)}"
            assert "peak_price" in trade
            tested += 1
        assert tested > 0

    def test_chart_invalid_id(self):
        r = requests.get(f"{BASE_URL}/api/autotrade/trades/does-not-exist-xyz/chart",
                         timeout=TIMEOUT)
        assert r.status_code in (404, 400), r.status_code


class TestIter10Regression:
    """Strategie-Filter ist clientseitig; serverseitig: status/limit/offset."""

    def test_closed_paging_and_total(self):
        r1 = requests.get(f"{BASE_URL}/api/autotrade/trades?status=closed&limit=3",
                          timeout=TIMEOUT)
        assert r1.status_code == 200
        d1 = r1.json()
        assert "total" in d1 and isinstance(d1["total"], int)
        assert len(d1["trades"]) <= 3
        r2 = requests.get(
            f"{BASE_URL}/api/autotrade/trades?status=closed&limit=3&offset=3",
            timeout=TIMEOUT)
        assert r2.status_code == 200
        d2 = r2.json()
        ids1 = {t["id"] for t in d1["trades"]}
        ids2 = {t["id"] for t in d2["trades"]}
        assert not (ids1 & ids2), "paging returns overlapping trades"

    def test_closed_trades_have_peak_and_trough_keys(self):
        r = requests.get(f"{BASE_URL}/api/autotrade/trades?status=closed&limit=10",
                         timeout=TIMEOUT)
        assert r.status_code == 200
        trades = r.json()["trades"]
        assert trades, "no closed trades"
        for t in trades:
            c = t["computed"]
            assert "trough_price" in c and "peak_price" in c
            if c["trough_price"] is not None:
                side = t["side"].upper()
                entry = float(t["entry"])
                if side == "LONG":
                    assert c["trough_price"] <= entry * 1.0000001
                else:
                    assert c["trough_price"] >= entry * 0.9999999
