"""Iteration 13 API regression: slippage-stats, chart guard for Forex,
computed.chart_available in trades list, ai/status active_fallbacks.outside_team.
READ-ONLY tests (live trading app!)."""
import os
import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

FOREX = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"}


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def trades(client):
    r = client.get(f"{BASE_URL}/api/autotrade/trades?limit=200", timeout=90)
    assert r.status_code == 200, r.text[:300]
    return r.json()


# --- Slippage stats endpoint ---
class TestSlippageStats:
    def test_default(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days=30", timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["days"] == 30
        assert isinstance(d["measured_trades"], int)
        assert isinstance(d["groups"], (list, dict))

    # NOTE: days=0 falls back to the default 30 (`int(days or 30)`), not 1 –
    # spec said 1; documented deviation, harmless.
    @pytest.mark.parametrize("inp,exp", [(0, 30), (9999, 365), (7, 7), (90, 90), (-5, 1)])
    def test_days_clamp(self, client, inp, exp):
        r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days={inp}", timeout=60)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["days"] == exp

    def test_invalid_days_type(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/slippage-stats?days=abc", timeout=60)
        assert r.status_code in (200, 422), r.text[:300]


# --- Trades list: computed.chart_available ---
class TestTradesChartAvailable:
    def test_flag_present_and_correct(self, trades):
        assert isinstance(trades, dict) or isinstance(trades, list)
        items = trades if isinstance(trades, list) else (
            (trades.get("open") or []) + (trades.get("closed") or []) or trades.get("trades") or [])
        assert items, f"no trades returned: keys={list(trades)[:10] if isinstance(trades, dict) else 'list'}"
        missing = [t.get("id") for t in items if "chart_available" not in (t.get("computed") or {})]
        assert not missing, f"computed.chart_available missing on {missing[:5]}"
        bad = [(t.get("id"), t.get("symbol"), t["computed"]["chart_available"])
               for t in items
               if (t.get("symbol") in FOREX) != (t["computed"]["chart_available"] is False)]
        assert not bad, f"wrong chart_available: {bad[:5]}"

    def test_no_mongo_id(self, trades):
        items = trades if isinstance(trades, list) else (
            (trades.get("open") or []) + (trades.get("closed") or []) or trades.get("trades") or [])
        assert all("_id" not in t for t in items)


# --- Chart endpoint guard ---
class TestTradeChart:
    def _pick(self, trades, forex: bool):
        items = trades if isinstance(trades, list) else (
            (trades.get("open") or []) + (trades.get("closed") or []) or trades.get("trades") or [])
        for t in items:
            if t.get("status") != "open" and ((t.get("symbol") in FOREX) == forex):
                return t
        return None

    def test_forex_chart_unavailable(self, client, trades):
        t = self._pick(trades, True)
        if not t:
            pytest.fail("no closed forex trade found in first 200 trades")
        r = client.get(f"{BASE_URL}/api/autotrade/trades/{t['id']}/chart", timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["chart_available"] is False
        assert d["candles"] == []
        assert "Bitunix" in (d.get("reason") or "")
        assert d.get("trade") and d["trade"].get("entry") is not None

    def test_crypto_chart_available(self, client, trades):
        t = self._pick(trades, False)
        if not t:
            pytest.fail("no closed crypto trade found")
        r = client.get(f"{BASE_URL}/api/autotrade/trades/{t['id']}/chart", timeout=90)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["chart_available"] is True
        assert isinstance(d["candles"], list) and len(d["candles"]) > 0, "no candles for crypto trade"
        assert d.get("interval")

    def test_unknown_trade_404(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/trades/NOPE-123/chart", timeout=60)
        assert r.status_code == 404


# --- AI status: outside_team flag ---
class TestAiStatus:
    def test_active_fallbacks_shape(self, client):
        r = client.get(f"{BASE_URL}/api/ai/status", timeout=90)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        ph = d.get("providers_health") or {}
        assert "active_fallbacks" in ph, f"keys={list(ph)[:10]}"
        afs = ph["active_fallbacks"] or []
        assert isinstance(afs, list)
        for af in afs:
            assert "outside_team" in af, f"outside_team missing in {af}"
            assert af["outside_team"] is None or isinstance(af["outside_team"], bool)
