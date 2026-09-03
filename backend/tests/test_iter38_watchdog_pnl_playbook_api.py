"""Iteration 38 – API-Smoke/Regression gegen den Dev-Server (localhost:8055).

READ-ONLY gegen echte Bitunix-Live-Keys: keine Orders/Closes/Trade-Actions.
Getestete Module:
  * services/position_watchdog.py  -> /api/autotrade/watchdog/status|config|run
  * services/entry_inflight.py     -> inflight_entries, Dedupe
  * services/pnl_reconcile.py      -> /api/autotrade/pnl-reconcile/status|run
  * services/ai_playbook.py        -> /api/ai/playbook (keine harten Sperren)
  * services/strategy_copilot.py   -> /api/copilot/chat (Einheiten USDT vs %)
  * Regression                     -> /api/autotrade/sync-status, /api/health
"""
import os
import re
from pathlib import Path

import pytest
import requests

BASE_URL = os.environ.get("KITRADER_BASE_URL", "http://localhost:8055").rstrip("/")

PHASES = {"live", "sammelt", "rückgestuft", "gesperrt"}


@pytest.fixture(scope="session")
def creds():
    """Admin-Zugang aus der Umgebung (backend/.env via conftest), Fallback:
    Notiz-Datei der Test-Umgebung."""
    if os.environ.get("ADMIN_PASSWORD"):
        return {"username": os.environ.get("ADMIN_USER", "Admin"),
                "password": os.environ["ADMIN_PASSWORD"]}
    p = Path("/app/memory/test_credentials.md")
    if not p.exists():
        pytest.skip("Keine Admin-Zugangsdaten (ADMIN_PASSWORD) gesetzt")
    m = re.search(r'\{"username":"([^"]+)","password":"([^"]+)"\}', p.read_text(encoding="utf-8"))
    if not m:
        pytest.skip("Keine Admin-Zugangsdaten gefunden")
    return {"username": m.group(1), "password": m.group(2)}


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin(client, creds):
    r = client.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"Admin-Login fehlgeschlagen: {r.status_code} {r.text[:300]}")
    token = r.json().get("token")
    if not token:
        pytest.fail(f"Kein Token in Login-Antwort: {r.text[:300]}")
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}"})
    return s


# --- Regression / Health -----------------------------------------------------
class TestHealthRegression:
    def test_health(self, client):
        r = client.get(f"{BASE_URL}/api/health", timeout=30)
        assert r.status_code == 200, r.text[:300]

    def test_sync_status(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/sync-status", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert "configured" in data
        assert data["configured"] is True
        assert "_id" not in data


# --- Watchdog ----------------------------------------------------------------
class TestWatchdogStatus:
    def test_status_contract(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/watchdog/status", timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert "_id" not in d
        assert d["settings"]["adopt_grace_sec"] == 90, d["settings"]
        assert isinstance(d["adopt_deferred"], int) and d["adopt_deferred"] >= 0
        assert isinstance(d["deduped"], int) and d["deduped"] >= 0
        assert isinstance(d["inflight_entries"], list)
        assert isinstance(d["errors"], list)


class TestWatchdogConfig:
    def test_config_clamping_and_reset(self, client, admin):
        def set_grace(v):
            r = admin.post(f"{BASE_URL}/api/autotrade/watchdog/config",
                           json={"adopt_grace_sec": v}, timeout=30)
            assert r.status_code == 200, r.text[:300]
            body = r.json()
            assert body["status"] == "success"
            return body["settings"]["adopt_grace_sec"]

        def read_grace():
            r = client.get(f"{BASE_URL}/api/autotrade/watchdog/status", timeout=30)
            assert r.status_code == 200
            return r.json()["settings"]["adopt_grace_sec"]

        try:
            assert set_grace(120) == 120
            assert read_grace() == 120           # persistiert
            assert set_grace(5000) == 900        # oberes Clamp
            assert read_grace() == 900
            assert set_grace(0) == 0             # Karenz aus erlaubt
            assert read_grace() == 0
        finally:
            assert set_grace(90) == 90
            assert read_grace() == 90

    def test_config_requires_admin(self, client):
        r = client.post(f"{BASE_URL}/api/autotrade/watchdog/config",
                        json={"adopt_grace_sec": 90}, timeout=30)
        assert r.status_code in (401, 403), f"{r.status_code} {r.text[:200]}"


class TestWatchdogRun:
    @pytest.mark.parametrize("attempt", [1, 2])
    def test_run_twice_no_errors(self, admin, attempt):
        r = admin.post(f"{BASE_URL}/api/autotrade/watchdog/run", timeout=120)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["status"] == "success", d
        res = d["result"]
        assert isinstance(res["positions"], int) and res["positions"] >= 0
        assert isinstance(res["adopt_deferred"], int) and res["adopt_deferred"] >= 0
        assert isinstance(res["deduped"], int) and res["deduped"] >= 0
        assert res["errors"] == [], res["errors"]

    def test_run_requires_admin(self, client):
        r = client.post(f"{BASE_URL}/api/autotrade/watchdog/run", timeout=60)
        assert r.status_code in (401, 403), f"{r.status_code} {r.text[:200]}"


# --- PnL-Reconcile -----------------------------------------------------------
class TestPnlReconcile:
    def test_status(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/pnl-reconcile/status", timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d.get("configured") is True, d
        assert "_id" not in d

    def test_run(self, admin, client):
        r = admin.post(f"{BASE_URL}/api/autotrade/pnl-reconcile/run", timeout=180)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["status"] == "success", d
        for k in ("checked", "reconciled", "changed"):
            assert isinstance(d[k], int) and d[k] >= 0, (k, d)
        # Status-Dokument wurde geschrieben
        s = client.get(f"{BASE_URL}/api/autotrade/pnl-reconcile/status",
                       timeout=30).json()
        assert s.get("last_run_at"), s

    def test_run_requires_admin(self, client):
        r = client.post(f"{BASE_URL}/api/autotrade/pnl-reconcile/run", timeout=60)
        assert r.status_code in (401, 403), f"{r.status_code} {r.text[:200]}"


# --- AI-Playbook (keine harten Sperren mehr) ---------------------------------
class TestPlaybook:
    def test_playbook_no_hard_locks(self, client):
        r = client.get(f"{BASE_URL}/api/ai/playbook", timeout=60)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["disabled"] == {}, d["disabled"]
        assert d["hard_locks"] is False
        assert isinstance(d["live_blocked"], dict)
        mat = d["maturity"]
        assert isinstance(mat, list)
        assert len(mat) == 13, f"{len(mat)} Einträge: {[m.get('setup') for m in mat]}"
        for m in mat:
            assert m.get("phase") in PHASES, m
            assert "paper_since_demotion" in m, m
        assert d["rules"]["promote_min_trades"] == 5, d["rules"]
        assert d["rules"]["demote_min_live_trades"] == 8, d["rules"]


# --- Trades / Dedupe ---------------------------------------------------------
class TestTradesDedupe:
    def test_open_trades_no_duplicate_position_ids(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/trades?status=open", timeout=60)
        assert r.status_code == 200, r.text[:300]
        payload = r.json()
        trades = payload if isinstance(payload, list) else payload.get("trades", [])
        assert isinstance(trades, list)
        seen = {}
        for t in trades:
            assert "_id" not in t
            if t.get("mode") != "live" or t.get("status") != "open":
                continue
            pid = t.get("bitunix_position_id")
            if not pid:
                continue
            seen.setdefault(str(pid), []).append(t.get("id"))
        dupes = {k: v for k, v in seen.items() if len(v) > 1}
        assert not dupes, f"Doppelte offene Live-Trades pro Position-ID: {dupes}"

    def test_adopted_trades_marked_manual(self, client):
        r = client.get(f"{BASE_URL}/api/autotrade/trades?status=open", timeout=60)
        assert r.status_code == 200
        payload = r.json()
        trades = payload if isinstance(payload, list) else payload.get("trades", [])
        ext = [t for t in trades if t.get("strategy_id") == "external"
               and not t.get("leftover")]
        for t in ext:
            assert t.get("strategy_name") == "Manuell (Bitunix)", t.get("strategy_name")
            assert t.get("manual_trade") is True, t
            assert t.get("bitunix_position_id"), t
        if not ext:
            pytest.skip("Keine vom Watchdog übernommenen Positionen offen")


# --- Copilot: Metriken mit Einheiten ----------------------------------------
class TestCopilotUnits:
    def test_drawdown_units(self, admin):
        body = {
            "message": ("Wie hoch ist der Drawdown relativ zum Startkapital? "
                        "Antworte in einem Satz."),
            "context": {
                "panel": "optimizer",
                "trade_params": {"max_capital": 10000},
                "result": {"metrics": {
                    "trades": 64, "wins": 36, "losses": 28, "win_rate": 56.2,
                    "pnl": 2410.5, "pnl_pct": 24.1, "max_drawdown": 1600,
                    "max_drawdown_pct": 16, "avg_pnl": 37.664}},
            },
        }
        r = admin.post(f"{BASE_URL}/api/copilot/chat", json=body, timeout=180)
        if r.status_code == 502:
            r = admin.post(f"{BASE_URL}/api/copilot/chat", json=body, timeout=180)
        assert r.status_code == 200, r.text[:400]
        reply = (r.json().get("reply") or "")
        assert reply.strip(), r.json()
        low = reply.lower()
        assert re.search(r"\b16(?:[.,]0)?\s*%", low) or "1600" in low, reply
        assert not re.search(r"1[.\s]?600\s*%", low), f"Falsche Einheit: {reply}"
