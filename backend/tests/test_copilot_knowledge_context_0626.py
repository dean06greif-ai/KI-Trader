# Copilot knowledge/context tests (06/2026): context-preview endpoint, regime-lab isolation,
# strategy copilot REGIME-ASSISTENT block, history window, chat error handling, assistant ampel.
import os
import time
import uuid

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")

FORBIDDEN_REGIME = ["Discovery", "Deep-Test", "MIN-TRADES", "STRATEGIE-ÜBERSICHT",
                    "Parameter-Optimierung", "Profit-Faktor", "bayes", "ERLAUBTE INDIKATOREN",
                    "GETEILTES KI-GEDÄCHTNIS", "LETZTES ERGEBNIS"]


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "PreviewAdmin2026"}, timeout=20)
    if r.status_code != 200:
        pytest.skip("login failed")
    return r.json()["token"]


@pytest.fixture(scope="module")
def H(token):
    return {"Authorization": f"Bearer {token}"}


def preview(H, panel, message="Was ist die Nutzen-Note?", **extra):
    r = requests.post(f"{BASE_URL}/api/copilot/context-preview",
                      json={"message": message, "context": {"panel": panel, **extra}},
                      headers=H, timeout=60)
    assert r.status_code == 200, r.text[:300]
    return r.json()


# --- context-preview auth
class TestPreviewAuth:
    def test_no_token(self):
        r = requests.post(f"{BASE_URL}/api/copilot/context-preview",
                          json={"message": "x", "context": {"panel": "regime_lab"}}, timeout=20)
        assert r.status_code in (401, 403)

    def test_bad_token(self):
        r = requests.post(f"{BASE_URL}/api/copilot/context-preview",
                          json={"message": "x", "context": {"panel": "regime_lab"}},
                          headers={"Authorization": "Bearer nope"}, timeout=20)
        assert r.status_code in (401, 403)


# --- regime lab copilot knowledge + isolation
class TestRegimeLab:
    def test_shape(self, H):
        d = preview(H, "regime_lab")
        assert d["panel"] == "regime_lab"
        assert d["system_chars"] == len(d["system"]) and d["prompt_chars"] == len(d["prompt"])
        assert "REGIME-COPILOT" in d["system"]

    def test_knowledge(self, H):
        d = preview(H, "regime_lab")
        s, p = d["system"], d["prompt"]
        assert "Regime-Assistent" in s and "NUTZEN-NOTE" in s
        for t in ["REGIME-WISSEN", "REGIME-ASSISTENT (Abschnitt 0", "MAKRO × HAUPT-MATRIX",
                  "NUTZEN-NOTE", "BEWERTUNG NEUER LÄUFE", "SCHUTZFILTER", "OVERFITTING-LEITSATZ",
                  "REGIME-ASSISTENT & BEWERTUNGS-MODUS", "Bewertung neuer Läufe:"]:
            assert t in p, t
        assert "Was ist die Nutzen-Note?" in p

    def test_isolation(self, H):
        d = preview(H, "regime_lab", result={"metrics": {"trades": 5}},
                    settings={"coins": ["BTCUSDT"], "timeframe": "15m", "days": 360})
        blob = d["system"] + d["prompt"]
        leaked = [t for t in FORBIDDEN_REGIME if t in blob]
        assert not leaked, leaked


# --- strategy copilot panels
class TestStrategyPanels:
    @pytest.mark.parametrize("panel", ["optimizer", "backtester", "builder"])
    def test_panel(self, H, panel):
        d = preview(H, panel, message="Soll ich robust als Objective nehmen?")
        assert d["panel"] == panel
        s, p = d["system"], d["prompt"]
        assert "STRATEGIE-COPILOT" in s
        assert "BEWERTUNG NEUER DYNAMIK-LÄUFE" in s
        assert 'Objective "robust"' in s and "combo|win_rate|pnl|robust" in s
        assert "Schutzfilter" in s and "Übergangsschutz" in s
        assert "beantworte ZUERST" in s and "~150" in s and "EINE Rückfrage" in s
        assert "REGIME-ASSISTENT & BEWERTUNGS-MODUS" in p
        assert "ERLAUBTE INDIKATOREN" in p

    def test_unknown_panel_defaults(self, H):
        d = preview(H, "zzz_unknown")
        assert d["panel"] != "regime_lab"


# --- chat failure path
class TestChat:
    def test_status(self):
        r = requests.get(f"{BASE_URL}/api/copilot/status", timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "ready" in d and "keys_available" in d

    def test_chat_empty(self, H):
        r = requests.post(f"{BASE_URL}/api/copilot/chat", json={"message": "  "}, headers=H, timeout=20)
        assert r.status_code == 400

    def test_chat_no_key_clean_error_not_stored(self, H):
        st = requests.get(f"{BASE_URL}/api/copilot/status", timeout=20).json()
        marker = f"TEST_copilot_{uuid.uuid4().hex[:8]}"
        r = requests.post(f"{BASE_URL}/api/copilot/chat",
                          json={"message": marker, "context": {"panel": "regime_lab"}},
                          headers=H, timeout=90)
        if st.get("ready"):
            pytest.skip("keys present – LLM path")
        assert r.status_code == 502, r.text[:300]
        # NOTE: Cloudflare replaces the backend's JSON 502 body with an HTML page on the public URL
        if r.headers.get("content-type", "").startswith("application/json"):
            assert "Copilot nicht erreichbar" in r.json()["detail"]
        h = requests.get(f"{BASE_URL}/api/copilot/history?panel=regime_lab&limit=60", timeout=20).json()
        assert not any(marker in (m.get("content") or "") for m in h["messages"])

    def test_chat_job_error(self, H):
        st = requests.get(f"{BASE_URL}/api/copilot/status", timeout=20).json()
        if st.get("ready"):
            pytest.skip("keys present")
        r = requests.post(f"{BASE_URL}/api/copilot/chat/start",
                          json={"message": "TEST_job", "context": {"panel": "optimizer"}},
                          headers=H, timeout=20)
        assert r.status_code in (200, 409)
        if r.status_code == 409:
            return
        jid = r.json()["job_id"]
        for _ in range(20):
            j = requests.get(f"{BASE_URL}/api/copilot/chat/job/{jid}", timeout=20).json()
            if j["status"] != "running":
                break
            time.sleep(1)
        assert j["status"] == "error" and j["error"]


# --- assistant ampel appears in context after a run
class TestAssistantAmpel:
    def test_crypto_ampel_line(self, H):
        r = requests.post(f"{BASE_URL}/api/regime-assistant/run",
                          json={"asset_class": "crypto", "symbols": ["BTCUSDT"], "days": 150},
                          headers=H, timeout=30)
        assert r.status_code in (200, 429), r.text[:300]
        if r.status_code == 200:
            jid = r.json().get("job_id")
            for _ in range(60):
                j = requests.get(f"{BASE_URL}/api/regime-assistant/job/{jid}", headers=H, timeout=20).json()
                if j.get("status") not in ("running", "queued", "pending"):
                    break
                time.sleep(3)
            assert j.get("status") in ("done", "completed", "finished"), str(j)[:300]
        for panel in ("regime_lab", "optimizer"):
            p = preview(H, panel)["prompt"]
            line = [ln for ln in p.splitlines() if ln.startswith("- Krypto")]
            assert line, f"no Krypto line in {panel}"
            assert "Ampel" in line[0] and "noch nicht geprüft" not in line[0], line[0]
