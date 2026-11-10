# Retest of iteration_10 fixes: assistant_lines (no 'None', grade/recommendation, per-symbol current
# lines, grau knowledge), sync /api/copilot/chat 503, opt_groups schema keys, isolation + admin-only preview.
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


@pytest.fixture(scope="module")
def H():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "PreviewAdmin2026"}, timeout=60)
    if r.status_code != 200:
        pytest.skip("login failed")
    return {"Authorization": f"Bearer {r.json()['token']}"}


def preview(H, panel, message="In welchem Regime ist BTC gerade?"):
    r = requests.post(f"{BASE_URL}/api/copilot/context-preview",
                      json={"message": message, "context": {"panel": panel}}, headers=H, timeout=60)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    return d["system"] + "\n" + d["prompt"]


# --- fresh crypto assistant run (module-level, once)
@pytest.fixture(scope="module")
def crypto_run(H):
    r = requests.post(f"{BASE_URL}/api/regime-assistant/run",
                      json={"asset_class": "crypto", "symbols": ["BTCUSDT"], "days": 150},
                      headers=H, timeout=30)
    if r.status_code in (409, 429):
        time.sleep(35)
        r = requests.post(f"{BASE_URL}/api/regime-assistant/run",
                          json={"asset_class": "crypto", "symbols": ["BTCUSDT"], "days": 150},
                          headers=H, timeout=30)
    assert r.status_code == 200, r.text[:300]
    jid = r.json()["job_id"]
    job = None
    for _ in range(100):
        time.sleep(3)
        job = requests.get(f"{BASE_URL}/api/regime-assistant/job/{jid}", timeout=20).json()
        if job.get("status") in ("done", "error"):
            break
    assert job and job.get("status") == "done", job
    return job


class TestAssistantLines:
    def test_run_has_current(self, crypto_run, H):
        # DB doc stores raw "results"; per-symbol "current" is in-memory (overview.latest)
        doc = requests.get(f"{BASE_URL}/api/regime-assistant/runs/latest",
                           params={"asset_class": "crypto"}, timeout=20).json()["run"]
        assert doc and doc["id"] == crypto_run["result"]["id"] and doc.get("results")
        ov = requests.get(f"{BASE_URL}/api/regime-assistant/overview", timeout=30).json()
        latest = [c for c in ov["classes"] if c["asset_class"] == "crypto"][0]["latest"]
        assert latest["id"] == doc["id"] and latest["current"][0]["symbol"] == "BTCUSDT"

    @pytest.mark.parametrize("panel", ["regime_lab", "optimizer"])
    def test_lines_in_context(self, crypto_run, H, panel):
        txt = preview(H, panel)
        krypto = [l for l in txt.splitlines() if l.startswith("- Krypto")]
        assert krypto, "no Krypto assistant line"
        line = krypto[0]
        assert "None" not in line, line
        assert "Empfehlung:" in line and "Ampel" in line
        assert "(" in line.split("Ampel", 1)[1]  # grade in brackets
        btc = [l for l in txt.splitlines() if "BTCUSDT jetzt:" in l]
        assert btc, "no per-symbol current line"
        b = btc[0]
        assert "Makro" in b and "Haupt" in b and "→" in b and "Long" in b and "Short" in b and "Größe ×" in b
        assert "None" not in b, b

    def test_grau_knowledge(self, H):
        # static knowledge block is regime_lab-only; optimizer sees grade text "(zu wenig Daten)" in the line
        assert "grau = zu wenig Daten" in preview(H, "regime_lab")

    def test_no_none_anywhere_in_assistant_block(self, H):
        txt = preview(H, "regime_lab")
        for l in txt.splitlines():
            if l.startswith("- ") and ("Ampel" in l or "jetzt:" in l):
                assert "None" not in l, l


class TestSyncChat:
    def test_chat_503(self, H):
        r = requests.post(f"{BASE_URL}/api/copilot/chat",
                          json={"message": "TEST_ hallo", "context": {"panel": "regime_lab"}},
                          headers=H, timeout=60)
        assert r.status_code == 503, r.status_code
        assert "Copilot nicht erreichbar" in r.json()["detail"]

    def test_chat_empty_400(self, H):
        r = requests.post(f"{BASE_URL}/api/copilot/chat", json={"message": ""}, headers=H, timeout=20)
        assert r.status_code == 400

    def test_chat_start_job_error(self, H):
        r = requests.post(f"{BASE_URL}/api/copilot/chat/start",
                          json={"message": "TEST_ hallo", "context": {"panel": "optimizer"}},
                          headers=H, timeout=30)
        assert r.status_code == 200
        jid = r.json()["job_id"]
        j = None
        for _ in range(20):
            time.sleep(1.5)
            j = requests.get(f"{BASE_URL}/api/copilot/chat/job/{jid}", timeout=20).json()
            if j.get("status") in ("done", "error"):
                break
        assert j["status"] == "error" and j.get("error"), j


class TestSchemaAndRegression:
    def test_opt_groups_keys(self, H):
        txt = preview(H, "optimizer")
        seg = txt[txt.index('"opt_groups"'):]
        seg = seg[:seg.index("}")]
        for k in ["tpsl", "breakeven", "trail", "profit_secure", "leverage", "auto_leverage",
                  "sessions", "time_exit", "entry_order", "direction"]:
            assert f'"{k}"' in seg, k
        assert '"sl"' not in seg and '"be"' not in seg

    def test_isolation(self, H):
        txt = preview(H, "regime_lab")
        for f in ["Discovery", "MIN-TRADES", "STRATEGIE-ÜBERSICHT", "ERLAUBTE INDIKATOREN",
                  "GETEILTES KI-GEDÄCHTNIS", '"opt_groups"']:
            assert f not in txt, f

    def test_preview_admin_only(self):
        r = requests.post(f"{BASE_URL}/api/copilot/context-preview",
                          json={"message": "x", "context": {"panel": "regime_lab"}}, timeout=20)
        assert r.status_code in (401, 403)
        r = requests.post(f"{BASE_URL}/api/copilot/context-preview",
                          json={"message": "x", "context": {"panel": "regime_lab"}},
                          headers={"Authorization": "Bearer nope"}, timeout=20)
        assert r.status_code in (401, 403)
