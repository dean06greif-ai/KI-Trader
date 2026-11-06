# AI role / structure-stop config + recommended settings + Telegram quiet/preset/stats API tests (public URL)
import os

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
AI_KEYS = ["ai_role", "structure_stop_enabled", "structure_tf", "structure_min_pct", "structure_max_pct"]
RESTORE_AI = {"ai_role": "frei", "structure_stop_enabled": False, "structure_tf": "15m",
              "structure_min_pct": 1.0, "structure_max_pct": 3.0}


def _cfg(s):
    d = s.get(f"{BASE_URL}/api/ai/status", timeout=20).json()
    return d.get("config", d)


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"username": "Admin", "password": "admin"}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    orig_ai = {k: _cfg(s).get(k) for k in AI_KEYS}
    orig_notify = s.get(f"{BASE_URL}/api/telegram/notify-config", timeout=20).json()
    orig_quiet = s.get(f"{BASE_URL}/api/telegram/quiet", timeout=20).json()
    print("ORIG_AI", orig_ai, "ORIG_QUIET", orig_quiet)
    yield s, orig_notify
    s.post(f"{BASE_URL}/api/ai/config", json=RESTORE_AI, timeout=20)
    nc = orig_notify.get("config", orig_notify) if isinstance(orig_notify, dict) else orig_notify
    rr = s.post(f"{BASE_URL}/api/telegram/notify-config", json=nc, timeout=20)
    s.post(f"{BASE_URL}/api/telegram/quiet", json={"enabled": False, "from_h": 23, "to_h": 7}, timeout=20)
    print("RESTORE notify", rr.status_code, {k: _cfg(s).get(k) for k in AI_KEYS})


# AI role + structure stop config
def test_ai_config_persist(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/ai/config", json={"ai_role": "pruefer", "structure_stop_enabled": True,
               "structure_tf": "1h", "structure_min_pct": 1.2, "structure_max_pct": 2.5}, timeout=20)
    assert r.status_code == 200, r.text
    c = _cfg(s)
    assert c["ai_role"] == "pruefer"
    assert c["structure_stop_enabled"] is True
    assert c["structure_tf"] == "1h"
    assert abs(c["structure_min_pct"] - 1.2) < 1e-6
    assert abs(c["structure_max_pct"] - 2.5) < 1e-6


def test_ai_role_invalid_ignored(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/ai/config", json={"ai_role": "boss"}, timeout=20)
    assert r.status_code in (200, 400)
    assert _cfg(s)["ai_role"] == "pruefer"


def test_ai_config_restore(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/ai/config", json=RESTORE_AI, timeout=20)
    assert r.status_code == 200
    c = _cfg(s)
    assert c["ai_role"] == "frei" and c["structure_stop_enabled"] is False
    assert c["structure_tf"] == "15m" and c["structure_min_pct"] == 1.0 and c["structure_max_pct"] == 3.0


# Recommended settings
def test_recommended_8_items():
    r = requests.get(f"{BASE_URL}/api/ai/recommended-settings", timeout=20)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 8, [i.get("key") for i in items]
    by = {i["key"]: i for i in items}
    assert by["ai_role"]["value"] == "pruefer"
    assert by["structure_stop_enabled"]["value"] is True


# Telegram stats / quiet / presets
def test_notify_stats():
    r = requests.get(f"{BASE_URL}/api/telegram/notify-stats", timeout=20)
    assert r.status_code == 200
    d = r.json()
    assert d["days"] == 7 and isinstance(d["counts"], dict)


def test_quiet_default():
    d = requests.get(f"{BASE_URL}/api/telegram/quiet", timeout=20).json()
    assert d["enabled"] is False and d["from_h"] == 23 and d["to_h"] == 7
    assert isinstance(d["critical"], list) and len(d["critical"]) > 0


def test_quiet_set_and_validation(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/telegram/quiet", json={"enabled": True, "from_h": 22, "to_h": 6}, timeout=20)
    assert r.status_code == 200, r.text
    d = requests.get(f"{BASE_URL}/api/telegram/quiet", timeout=20).json()
    assert d["enabled"] is True and d["from_h"] == 22 and d["to_h"] == 6
    assert s.post(f"{BASE_URL}/api/telegram/quiet", json={"from_h": "x"}, timeout=20).status_code == 400
    assert requests.post(f"{BASE_URL}/api/telegram/quiet", json={"enabled": True}, timeout=20).status_code == 401
    s.post(f"{BASE_URL}/api/telegram/quiet", json={"enabled": False, "from_h": 23, "to_h": 7}, timeout=20)
    d = requests.get(f"{BASE_URL}/api/telegram/quiet", timeout=20).json()
    assert d["enabled"] is False and d["from_h"] == 23 and d["to_h"] == 7


def test_preset_wichtig(admin):
    s, _ = admin
    r = s.post(f"{BASE_URL}/api/telegram/notify-preset", json={"preset": "wichtig"}, timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    c = d.get("config", d)
    assert c["kill_switch"] is True and c["signals"] is False and c["backtest_done"] is False
    assert s.post(f"{BASE_URL}/api/telegram/notify-preset", json={"preset": "bogus"}, timeout=20).status_code == 400
    assert requests.post(f"{BASE_URL}/api/telegram/notify-preset", json={"preset": "wichtig"}, timeout=20).status_code == 401


def test_notify_restore(admin):
    s, orig = admin
    nc = orig.get("config", orig)
    r = s.post(f"{BASE_URL}/api/telegram/notify-config", json=nc, timeout=20)
    assert r.status_code == 200
    now = s.get(f"{BASE_URL}/api/telegram/notify-config", timeout=20).json()
    assert now == orig
