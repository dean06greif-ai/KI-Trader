"""Regressionstests: Confluence-API + Fee-Wächter-Lockerung (Read/Write via API)."""
import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    env_path = "/app/frontend/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break

TIMEOUT = 15


def _token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={
        "username": os.environ.get("ADMIN_USER", "Admin"),
        "password": os.environ.get("ADMIN_PASSWORD", "Dean06Greif!/Admin")},
        timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    return r.json()["token"]


class TestConfluenceAPI:
    def test_config_shape(self):
        r = requests.get(f"{BASE_URL}/api/confluence/config", timeout=TIMEOUT)
        assert r.status_code == 200
        cfg = r.json()
        for key in ("enabled", "window_min", "min_strategies", "boost_enabled",
                    "capital_boost", "notify_enabled", "ai_context_enabled"):
            assert key in cfg, f"{key} fehlt in {cfg}"

    def test_config_save_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/confluence/config",
                          json={"capital_boost": 2.0}, timeout=TIMEOUT)
        assert r.status_code in (401, 403)

    def test_config_roundtrip_and_clamp(self):
        tok = _token()
        h = {"Authorization": f"Bearer {tok}"}
        orig = requests.get(f"{BASE_URL}/api/confluence/config", timeout=TIMEOUT).json()
        try:
            r = requests.post(f"{BASE_URL}/api/confluence/config", headers=h,
                              json={"capital_boost": 99, "window_min": 999,
                                    "min_strategies": 1}, timeout=TIMEOUT)
            assert r.status_code == 200
            cfg = r.json()
            assert cfg["capital_boost"] <= 3.0
            assert cfg["window_min"] <= 120
            assert cfg["min_strategies"] >= 2
        finally:
            requests.post(f"{BASE_URL}/api/confluence/config", headers=h,
                          json=orig, timeout=TIMEOUT)

    def test_events_endpoint(self):
        r = requests.get(f"{BASE_URL}/api/confluence/events?limit=5", timeout=TIMEOUT)
        assert r.status_code == 200
        assert isinstance(r.json().get("events"), list)

    def test_stats_endpoint(self):
        r = requests.get(f"{BASE_URL}/api/confluence/stats", timeout=TIMEOUT)
        assert r.status_code == 200
        d = r.json()
        assert "confluence" in d and "normal" in d
        for side in ("confluence", "normal"):
            for k in ("total", "closed", "wins", "win_rate", "pnl"):
                assert k in d[side]


class TestFeeGuardRelax:
    def test_ai_config_fee_guard_relaxed(self):
        tok = _token()
        r = requests.get(f"{BASE_URL}/api/ai/config", timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {tok}"})
        if r.status_code != 200:
            r = requests.get(f"{BASE_URL}/api/ai/settings", timeout=TIMEOUT,
                             headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200, r.text
        cfg = r.json()
        cfg = cfg.get("config", cfg)
        assert float(cfg.get("fee_guard_mult", 0)) <= 4.0
        assert float(cfg.get("fee_guard_atr_mult", 0)) <= 4.0
