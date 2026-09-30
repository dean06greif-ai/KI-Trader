"""Iteration 33 Review (Testing-Agent): RAM-Queue E2E light + per-Worker data_dir.

Sparsam: genau EIN kurzer Cloud-Backtest (1 Coin, 1 Tag).
"""
import os
import time

import pytest
import requests
from dotenv import dotenv_values

_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _env.get("REACT_APP_BACKEND_URL")).rstrip("/")
TIMEOUT = 60


@pytest.fixture(scope="module")
def admin_headers():
    r = requests.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"})
    assert r.status_code == 200, r.text[:300]
    token = r.json().get("token")
    assert token
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def worker_token(admin_headers):
    r = requests.get(f"{BASE_URL}/api/localworker/token", headers=admin_headers,
                     timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    tok = r.json().get("token")
    assert isinstance(tok, str) and tok
    return tok


# ---- RAM-Queue: Cloud-Backtest (kurz) ----
class TestRamQueueE2E:
    def test_short_cloud_backtest_completes(self, admin_headers):
        sr = requests.get(f"{BASE_URL}/api/strategies", headers=admin_headers,
                          timeout=TIMEOUT)
        assert sr.status_code == 200, sr.text[:300]
        data = sr.json()
        items = data if isinstance(data, list) else data.get("strategies") or []
        assert items, "keine Strategien vorhanden"
        sid = items[0]["id"]

        body = {"strategy_ids": [sid], "symbols": ["BTCUSDT"], "days": 1,
                "execution": "cloud"}
        r = requests.post(f"{BASE_URL}/api/backtest/run", json=body,
                          headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:400]
        payload = r.json()
        assert payload["status"] == "started"
        assert "ram_queued" in payload, f"Feld ram_queued fehlt: {payload}"
        assert isinstance(payload["ram_queued"], bool)
        assert payload["ram_queued"] is False  # Dev-Pod hat RAM frei
        job_id = payload["job_id"]

        final = None
        for _ in range(60):
            time.sleep(2)
            st = requests.get(f"{BASE_URL}/api/backtest/status/{job_id}",
                              timeout=TIMEOUT)
            assert st.status_code == 200, st.text[:300]
            js = st.json()
            if js.get("status") in ("completed", "done", "error", "cancelled"):
                final = js
                break
        assert final is not None, "Backtest nicht in 120s fertig"
        assert final["status"] in ("completed", "done"), final.get("error")
        assert final.get("result") is not None


# ---- Per-Worker data_dir ----
class TestPerWorkerDataDirE2E:
    def test_set_poll_and_cleanup(self, admin_headers, worker_token):
        r = requests.post(f"{BASE_URL}/api/localworker/worker/testA/data-dir",
                          json={"data_dir": "D:/nur-testA"},
                          headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert r.json()["settings"]["data_dirs"]["testA"] == "D:/nur-testA"

        pa = requests.post(f"{BASE_URL}/api/worker/poll",
                           headers={"X-Worker-Token": worker_token},
                           json={"worker_id": "testA", "want_compute": False,
                                 "want_data": False}, timeout=TIMEOUT)
        assert pa.status_code == 200, pa.text[:300]
        assert pa.json()["settings"]["data_dir"] == "D:/nur-testA"
        assert "data_dirs" not in pa.json()["settings"]

        pb = requests.post(f"{BASE_URL}/api/worker/poll",
                           headers={"X-Worker-Token": worker_token},
                           json={"worker_id": "testB", "want_compute": False,
                                 "want_data": False}, timeout=TIMEOUT)
        assert pb.status_code == 200
        assert pb.json()["settings"]["data_dir"] != "D:/nur-testA"

        # Persistenz prüfen (Status-Endpoint) inkl. data_dir_override
        stt = requests.get(f"{BASE_URL}/api/localworker/status",
                           headers=admin_headers, timeout=TIMEOUT)
        assert stt.status_code == 200, stt.text[:300]
        sj = stt.json()
        assert isinstance(sj["settings"].get("data_dirs"), dict)
        assert sj["settings"]["data_dirs"].get("testA") == "D:/nur-testA"
        for w in sj.get("workers") or []:
            assert "data_dir_override" in w, f"data_dir_override fehlt: {w}"

        # Aufräumen
        r = requests.post(f"{BASE_URL}/api/localworker/worker/testA/data-dir",
                          json={"data_dir": ""}, headers=admin_headers,
                          timeout=TIMEOUT)
        assert r.status_code == 200
        assert "testA" not in r.json()["settings"]["data_dirs"]

    def test_datadir_endpoint_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/localworker/worker/testA/data-dir",
                          json={"data_dir": "D:/x"}, timeout=TIMEOUT)
        assert r.status_code in (401, 403), r.status_code

    def test_poll_requires_worker_token(self):
        r = requests.post(f"{BASE_URL}/api/worker/poll",
                          json={"worker_id": "testA"}, timeout=TIMEOUT)
        assert r.status_code in (401, 403), r.status_code
