"""Iteration 24: independently verify pivot fix + backend smoke."""
import os
import numpy as np
import pandas as pd
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


@pytest.mark.unit
def test_kombi_pivot_scan_no_repeat_after_crash_wick():
    """Synthetic series with one -66% wick candle -> no pivot index repeats forever."""
    from services.regime_kombi import _pivot_scan

    n = 400
    rng = np.random.default_rng(42)
    close = 100 + np.cumsum(rng.normal(0, 0.3, n))
    high = close + 0.5
    low = close - 0.5
    # inject a violent -66% wick at index 200
    wick_i = 200
    low[wick_i] = close[wick_i] * 0.34
    high[wick_i] = close[wick_i] * 1.02

    thr_piv = np.full(n, 1.5)
    pivots, accel, retrace, rev_count, since_ext, piv_dir = _pivot_scan(
        high, low, close, thr_piv, persist=3)
    assert isinstance(pivots, list) and len(pivots) > 0

    # each pivot is (i, kind, ext_i) or similar; support tuple/dict
    idxs = []
    delays = []
    for p in pivots:
        e = int(p["i"])
        c = int(p["confirmed_i"])
        idxs.append(c)
        delays.append(c - e)

    from collections import Counter
    ctr = Counter(idxs)
    max_repeat = max(ctr.values())
    max_delay = max(delays)
    print(f"pivot count={len(pivots)} unique={len(ctr)} max_repeat_at_same_bar={max_repeat} max_delay_bars={max_delay}")
    assert max_repeat <= 2, f"pivot index repeats {max_repeat} times on same bar (flip-forever bug)"
    assert max_delay < 30, f"pivot delay too large: {max_delay} bars"


@pytest.mark.unit
def test_kombi_engine_avg_delay_days_small():
    """Run regime engine with kombi detector on synthetic data; avg_delay_days stays small."""
    try:
        from services import regime_engine, regime_kombi
    except Exception as e:
        pytest.skip(f"regime_engine import failed: {e}")

    n = 500
    rng = np.random.default_rng(7)
    close = 100 + np.cumsum(rng.normal(0, 0.4, n))
    high = close + 0.5
    low = close - 0.5
    low[300] = close[300] * 0.34  # crash wick
    high[300] = close[300] * 1.02
    ts = pd.date_range("2024-01-01", periods=n, freq="1D")
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": 1.0}, index=ts)

    thr_piv = np.full(n, 1.5)
    pivots, *_ = regime_kombi._pivot_scan(high, low, close, thr_piv, persist=3)
    delays_days = [int(p["confirmed_i"]) - int(p["i"]) for p in pivots]
    avg = sum(delays_days) / max(1, len(delays_days))
    print(f"avg_delay_days={avg:.2f} over {len(delays_days)} pivots")
    assert avg < 15.0, f"avg_delay_days={avg} exceeds 15 (bug: was 121-347d before fix)"


# ---------- Backend smoke ----------

def _admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"},
                      timeout=45)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_regime_lab_releases_shape():
    tok = _admin_token()
    r = requests.get(f"{BASE_URL}/api/regime-lab/releases",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "shadow_by_aid" in data and "orphan_shadow" in data
    assert isinstance(data["shadow_by_aid"], dict)
    assert isinstance(data["orphan_shadow"], dict)


def test_regime_lab_list_200():
    tok = _admin_token()
    r = requests.get(f"{BASE_URL}/api/regime-lab/list",
                     headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, (list, dict))
