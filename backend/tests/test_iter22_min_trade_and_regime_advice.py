"""Iteration 22 backend tests:
- /api/min-trade/stats?days=90 shape and expected counts from seeded test data
- /api/regime-lab/autopilot/advice with various parameter combos
"""
import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or os.environ.get(
    "BASE_URL", ""
).rstrip("/")
if not BASE_URL:
    # Fallback: read frontend .env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass


_TOKEN_CACHE = {"tok": None}


def _admin_token():
    if _TOKEN_CACHE["tok"]:
        return _TOKEN_CACHE["tok"]
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": "Admin", "password": "Dean06Greif!/Admin"},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    _TOKEN_CACHE["tok"] = r.json()["token"]
    return _TOKEN_CACHE["tok"]


# ---------------------------- min-trade/stats ---------------------------------
def test_min_trade_stats_shape_and_counts():
    tok = _admin_token()
    r = requests.get(
        f"{BASE_URL}/api/min-trade/stats?days=90",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=90,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("days", "min_trades", "normal", "rows"):
        assert k in data, f"Missing key {k}"
    assert data["days"] == 90

    mt = data["min_trades"]
    for k in ("trades", "open", "wins", "winrate", "pnl", "fees", "avg_pnl", "r_multiple"):
        assert k in mt, f"min_trades missing {k}"

    nm = data["normal"]
    for k in ("trades", "open", "wins", "winrate", "pnl", "fees", "avg_pnl", "r_multiple"):
        assert k in nm, f"normal missing {k}"

    # Expected seeded counts
    assert mt["trades"] == 2, f"expected 2 min closed trades, got {mt}"
    assert mt["open"] == 1, f"expected 1 open min trade, got {mt}"
    assert mt["winrate"] == 50, f"expected winrate 50, got {mt['winrate']}"
    assert nm["trades"] == 35, f"expected 35 normal trades, got {nm['trades']}"

    ids = [row.get("id") for row in data["rows"]]
    for tid in ("test_mt_0", "test_mt_1", "test_mt_2"):
        assert tid in ids, f"seeded row {tid} missing (got {ids})"


def test_min_trade_stats_days_param():
    tok = _admin_token()
    r = requests.get(
        f"{BASE_URL}/api/min-trade/stats?days=30",
        headers={"Authorization": f"Bearer {tok}"},
        timeout=90,
    )
    assert r.status_code == 200
    assert r.json()["days"] == 30


# --------------------- regime-lab/autopilot/advice ----------------------------
def test_regime_advice_implausible_returns_advice():
    tok = _admin_token()
    r = requests.get(
        f"{BASE_URL}/api/regime-lab/autopilot/advice",
        params={
            "timeframe": "15m",
            "days": 1080,
            "n_symbols": 11,
            "min_days": 1,
            "max_days": 0,
        },
        headers={"Authorization": f"Bearer {tok}"},
        timeout=90,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert "advice" in data
    assert isinstance(data["advice"], list)
    assert len(data["advice"]) >= 1, f"expected non-empty advice, got {data}"


def test_regime_advice_plausible_returns_ok():
    tok = _admin_token()
    r = requests.get(
        f"{BASE_URL}/api/regime-lab/autopilot/advice",
        params={
            "timeframe": "1h",
            "days": 720,
            "n_symbols": 5,
            "min_days": 4,
            "max_days": 14,
        },
        headers={"Authorization": f"Bearer {tok}"},
        timeout=90,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data.get("advice"), list)
    assert data["advice"] == [], f"expected empty advice, got {data['advice']}"
    band = data.get("recommended_band")
    assert band == [4, 14] or band == (4, 14), f"expected [4,14], got {band}"
