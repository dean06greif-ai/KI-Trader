"""Iter27 review tests: coin master switch, per-strategy override, signal-mode toggle."""
import os, requests, pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://trading-refactor-5.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"username": "Admin", "password": "PreviewAdmin123"}, timeout=60)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_coin_master_off_then_on(hdr):
    # switch HYPEUSDT off
    r = requests.post(f"{BASE}/api/autotrade/coin/HYPEUSDT",
                      headers=hdr, json={"enabled": False}, timeout=15)
    assert r.status_code == 200, r.text

    # override per strategy: paper + signals_enabled
    r2 = requests.post(f"{BASE}/api/autotrade/strategy/ai_trader/coin/HYPEUSDT",
                       headers=hdr,
                       json={"mode": "paper", "enabled": True, "signals_enabled": True},
                       timeout=15)
    assert r2.status_code == 200, r2.text

    # verify config shows master OFF
    cfg = requests.get(f"{BASE}/api/autotrade/config", headers=hdr, timeout=15)
    assert cfg.status_code == 200, cfg.text
    data = cfg.json()
    coins = (data.get("config") or data).get("coins") or data.get("coins") or {}
    hy = coins.get("HYPEUSDT") or {}
    assert hy.get("enabled") is False, f"Expected HYPEUSDT master OFF, got {hy}"

    # restore master ON
    r3 = requests.post(f"{BASE}/api/autotrade/coin/HYPEUSDT",
                       headers=hdr, json={"enabled": True}, timeout=15)
    assert r3.status_code == 200


def test_telegram_notify_config_signal_mode(hdr):
    # get current
    r = requests.get(f"{BASE}/api/telegram/notify-config", headers=hdr, timeout=15)
    assert r.status_code == 200, r.text
    orig = r.json()
    # ensure key exists
    assert "signals_only_traded" in orig, orig

    # flip to 'every signal'
    r2 = requests.post(f"{BASE}/api/telegram/notify-config",
                       headers=hdr, json={"signals_only_traded": False}, timeout=15)
    assert r2.status_code == 200, r2.text

    r3 = requests.get(f"{BASE}/api/telegram/notify-config", headers=hdr, timeout=15)
    assert r3.json().get("signals_only_traded") is False

    # restore to true (default)
    r4 = requests.post(f"{BASE}/api/telegram/notify-config",
                       headers=hdr, json={"signals_only_traded": True}, timeout=15)
    assert r4.status_code == 200
    r5 = requests.get(f"{BASE}/api/telegram/notify-config", headers=hdr, timeout=15)
    assert r5.json().get("signals_only_traded") is True
