"""Live smoke test for iteration 50: orderflow (crypto/commodity/forex), ml gate size_factor, ai status."""
import os
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://ml-fx-pro.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


def _login():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    return s


def test_orderflow_gold_maps_to_xauusdt():
    s = _login()
    r = s.get(f"{BASE}/api/ai/orderflow/GOLD", timeout=20)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    print("GOLD:", {k: body.get(k) for k in ("of_symbol", "connected", "n_ticks")}, "text_len=", len(body.get("text") or ""))
    assert body.get("of_symbol") == "XAUUSDT", f"of_symbol mismatch: {body.get('of_symbol')}"
    assert body.get("text") is not None, "text is null - expected real Bitunix flow or CME futures approximation"


def test_orderflow_status_includes_new_symbols():
    s = _login()
    expected = {"XAUUSDT", "XAGUSDT", "CLUSDT", "QQQUSDT", "SPYUSDT"}
    r = s.get(f"{BASE}/api/ai/orderflow/GOLD", timeout=20)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    status_symbols = set((body.get("status") or {}).get("symbols") or [])
    print("Subscribed symbols:", sorted(status_symbols))
    missing = expected - status_symbols
    assert not missing, f"Missing symbols in Orderflow subscription: {missing}"
    # UI-alias mapping checks
    ui_map = {"GOLD": "XAUUSDT", "SILVER": "XAGUSDT", "OIL": "CLUSDT"}
    for ui_sym, expected_of in ui_map.items():
        r = s.get(f"{BASE}/api/ai/orderflow/{ui_sym}", timeout=20)
        assert r.status_code == 200, f"{ui_sym}: {r.status_code}"
        body = r.json()
        print(f"{ui_sym} -> of_symbol={body.get('of_symbol')}")
        assert body.get("of_symbol") == expected_of


def test_orderflow_eurusd_futures_proxy():
    s = _login()
    r = s.get(f"{BASE}/api/ai/orderflow/EURUSD", timeout=20)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    print("EURUSD body keys:", list(body.keys()))
    print("EURUSD fx_futures_proxy:", (body.get("fx_futures_proxy") or "")[:300])
    # Business rule: on weekday (Thursday now) forex market open -> proxy should include 6E=F
    txt = body.get("fx_futures_proxy") or body.get("text") or ""
    assert "6E=F" in txt or "6E" in txt or body.get("fx_futures_proxy") is not None, \
        f"expected CME futures approximation with 6E=F, got: {txt[:200]}"


def test_orderflow_btcusdt_regression():
    s = _login()
    r = s.get(f"{BASE}/api/ai/orderflow/BTCUSDT", timeout=20)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    print("BTC:", {k: body.get(k) for k in ("of_symbol", "connected", "n_ticks")}, "text_len=", len(body.get("text") or ""))
    assert body.get("of_symbol") == "BTCUSDT"


def test_ml_gate_status_size_factor():
    s = _login()
    r = s.get(f"{BASE}/api/ml/gate/status", timeout=20)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    print("ML gate:", body)
    assert "size_factor" in body, "size_factor missing"
    assert body["size_factor"] in (0.5, 1.0), f"unexpected size_factor: {body['size_factor']}"
    assert "size_factor_reason" in body, "size_factor_reason missing"
    # Regression
    assert body.get("mode") == "shadow", f"mode should be shadow, got {body.get('mode')}"
    assert "version" in body
    assert "metrics" in body


def test_ai_status_regression():
    s = _login()
    r = s.get(f"{BASE}/api/ai/status", timeout=30)
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    print("ai/status keys:", list(body.keys())[:20])
    # sanity: should have some providers info
    assert isinstance(body, dict) and len(body) > 0
