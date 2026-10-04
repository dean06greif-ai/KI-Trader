"""Iteration 12 API-Tests: Baustein A (Low-Vol-Market-Block Config) +
Baustein B (Slippage-/Fill-Qualitäts-Endpoint).

WICHTIG: LIVE-Bitunix-Keys – es werden KEINE Orders/Trades ausgelöst.
Der Config-Test setzt low_vol_atr_threshold_pct sofort auf 0.1 zurück.
"""
import os

import pytest
import requests
from dotenv import dotenv_values

_fe = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("BACKEND_BASE_URL")
            or _fe.get("REACT_APP_BACKEND_URL")
            or "http://localhost:8001").rstrip("/")

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


# Der Backend-Event-Loop wird während des KI-Analyse-Zyklus (candle_cache
# EXTEND-HEAD) für ~20-30 s blockiert -> großzügiges Timeout + ein Retry.
TIMEOUT = 120


def _get(session, path, retries=2):
    last = None
    for _ in range(retries + 1):
        try:
            return session.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
        except requests.exceptions.RequestException as e:
            last = e
    raise AssertionError(f"GET {path} nach Retries fehlgeschlagen: {last}")


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin(client):
    """Admin-Session (Cookie oder Bearer-Token, je nach Implementierung)."""
    for payload in ({"username": ADMIN_USER, "password": ADMIN_PASS},
                    {"user": ADMIN_USER, "password": ADMIN_PASS},
                    {"password": ADMIN_PASS}):
        r = client.post(f"{BASE_URL}/api/auth/login", json=payload, timeout=TIMEOUT)
        if r.status_code == 200:
            try:
                tok = r.json().get("token") or r.json().get("access_token")
            except Exception:
                tok = None
            if tok:
                client.headers.update({"Authorization": f"Bearer {tok}"})
            return client
    pytest.fail(f"Admin-Login fehlgeschlagen: {r.status_code} {r.text[:300]}")


# ---------------- Baustein B: Slippage-Stats-Endpoint ----------------
class TestSlippageStatsEndpoint:
    def test_default_shape(self, client):
        r = _get(client, "/api/autotrade/slippage-stats?days=30")
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["days"] == 30
        assert isinstance(d["measured_trades"], int)
        assert isinstance(d["groups"], list)
        assert len(d["groups"]) <= max(d["measured_trades"], 0) or d["measured_trades"] == 0

    @pytest.mark.parametrize("given,expected", [(0, 30), (1, 1), (365, 365), (9999, 365), (-5, 1)])
    def test_days_clamped(self, client, given, expected):
        r = _get(client, f"/api/autotrade/slippage-stats?days={given}")
        assert r.status_code == 200, r.text[:300]
        assert r.json()["days"] == expected

    def test_groups_schema_when_present(self, client):
        d = _get(client, "/api/autotrade/slippage-stats").json()
        for g in d["groups"]:
            for k in ("strategy_id", "mode", "order_kind", "trades",
                      "avg_slippage_pct", "total_slippage_usdt",
                      "avg_mfe_pct", "avg_mae_pct"):
                assert k in g, f"Feld {k} fehlt in {g}"
            assert g["order_kind"] in ("market", "maker", "taker_fallback", "limit_fill")

    def test_no_mongo_id_leak(self, client):
        assert "_id" not in _get(client, "/api/autotrade/slippage-stats").text


# ---------------- Baustein A: Config low_vol_* ----------------
class TestLowVolConfig:
    def test_status_exposes_low_vol_defaults(self, client):
        r = _get(client, "/api/ai/status")
        assert r.status_code == 200
        cfg = r.json().get("config") or {}
        assert cfg.get("low_vol_market_block_enabled") is True
        assert float(cfg.get("low_vol_atr_threshold_pct")) == pytest.approx(0.1)

    def test_config_requires_admin(self, client):
        plain = requests.Session()
        r = plain.post(f"{BASE_URL}/api/ai/config",
                       json={"low_vol_atr_threshold_pct": 0.2}, timeout=TIMEOUT)
        assert r.status_code in (401, 403), f"ungeschützt: {r.status_code}"

    def test_threshold_clamped_and_restored(self, admin):
        try:
            r = admin.post(f"{BASE_URL}/api/ai/config",
                           json={"low_vol_atr_threshold_pct": 5}, timeout=TIMEOUT)
            assert r.status_code == 200, r.text[:300]
            assert float(r.json()["config"]["low_vol_atr_threshold_pct"]) == pytest.approx(2.0)

            r = admin.post(f"{BASE_URL}/api/ai/config",
                           json={"low_vol_atr_threshold_pct": -3}, timeout=TIMEOUT)
            assert r.status_code == 200
            assert float(r.json()["config"]["low_vol_atr_threshold_pct"]) == pytest.approx(0.0)

            # Persistenz-Check über /api/ai/status
            got = _get(admin, "/api/ai/status").json()["config"]
            assert float(got["low_vol_atr_threshold_pct"]) == pytest.approx(0.0)
        finally:
            # ORIGINALWERT WIEDERHERSTELLEN (User-Vorgabe: 0.10 %)
            rr = admin.post(f"{BASE_URL}/api/ai/config",
                            json={"low_vol_atr_threshold_pct": 0.1,
                                  "low_vol_market_block_enabled": True}, timeout=TIMEOUT)
            assert rr.status_code == 200
            assert float(rr.json()["config"]["low_vol_atr_threshold_pct"]) == pytest.approx(0.1)

    def test_restored_state(self, client):
        cfg = _get(client, "/api/ai/status").json()["config"]
        assert float(cfg["low_vol_atr_threshold_pct"]) == pytest.approx(0.1)
        assert cfg["low_vol_market_block_enabled"] is True


# ---------------- Regression: Trades-Endpoints ----------------
class TestTradesRegression:
    def test_trades_computed_fields(self, client):
        r = _get(client, "/api/autotrade/trades?limit=5")
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        rows = data["trades"] if isinstance(data, dict) else data
        assert isinstance(rows, list)
        for t in rows:
            comp = t.get("computed") or {}
            for k in ("mfe_pct", "mae_pct", "peak_price", "trough_price"):
                assert k in comp, f"computed-Feld {k} fehlt"

    def test_trades_paging_total(self, client):
        r = _get(client, "/api/autotrade/trades?status=closed&offset=0&limit=5")
        assert r.status_code == 200
        d = r.json()
        assert isinstance(d, dict) and isinstance(d.get("total"), int)
        assert len(d["trades"]) <= 5
